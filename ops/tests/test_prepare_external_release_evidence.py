from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType

import pytest

TEST_GIT_SHA = "a" * 40


def _load_preparer() -> ModuleType:
    script = Path(__file__).parents[2] / "scripts/prepare_external_release_evidence.py"
    spec = importlib.util.spec_from_file_location("prepare_external_release_evidence", script)
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load external evidence preparer")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _write_sources(source: Path, *, generated_at: datetime | None = None) -> None:
    source.mkdir()
    identity = {
        "status": "passed",
        "generated_at": (generated_at or datetime.now(UTC)).isoformat(),
        "git_sha": TEST_GIT_SHA,
        "environment": "staging",
    }
    for filename in (
        "alertmanager-notification.json",
        "dr-release.json",
        "email-delivery.json",
    ):
        (source / filename).write_text(json.dumps(identity), encoding="utf-8")
    (source / "alertmanager.yml").write_text(
        "route:\n  receiver: ops\nreceivers:\n  - name: ops\n",
        encoding="utf-8",
    )


def _validator_evidence(
    reference: str,
    *,
    image_id_character: str,
    architecture: str = "amd64",
) -> dict[str, str]:
    return {
        "reference": reference,
        "manifest_list_digest": reference.rsplit("@", maxsplit=1)[1],
        "image_id": "sha256:" + image_id_character * 64,
        "operating_system": "linux",
        "architecture": architecture,
        "docker_architecture": architecture,
    }


def test_preparer_copies_real_inputs_and_binds_validator_results(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_sources(source)
    original_dr = (source / "dr-release.json").read_bytes()

    def fake_checks(**kwargs: object) -> tuple[dict[str, str], dict[str, str]]:
        assert kwargs["alertmanager_config"] == output / "alertmanager.yml"
        (source / "dr-release.json").write_text(
            "runner-secret-that-must-not-be-copied",
            encoding="utf-8",
        )
        return (
            _validator_evidence(
                preparer.PROMETHEUS_IMAGE,
                image_id_character="b",
            ),
            _validator_evidence(
                preparer.ALERTMANAGER_IMAGE,
                image_id_character="c",
            ),
        )

    monkeypatch.setattr(
        preparer,
        "_run_observability_checks",
        fake_checks,
    )

    files = preparer.prepare(
        source_dir=source,
        output_dir=output,
        git_sha=TEST_GIT_SHA,
        environment="staging",
        prometheus_image=preparer.PROMETHEUS_IMAGE,
        alertmanager_image=preparer.ALERTMANAGER_IMAGE,
    )

    assert {path.name for path in files} == set(preparer.OUTPUT_FILES)
    promtool = json.loads((output / "promtool.json").read_text(encoding="utf-8"))
    assert promtool["status"] == "passed"
    assert promtool["prometheus_image_id"] == "sha256:" + "b" * 64
    assert promtool["alertmanager_image_id"] == "sha256:" + "c" * 64
    assert promtool["prometheus_image"] == preparer.PROMETHEUS_IMAGE
    assert (
        promtool["prometheus_manifest_list_digest"]
        == (preparer.PROMETHEUS_IMAGE.rsplit("@", maxsplit=1)[1])
    )
    assert promtool["prometheus_image_architecture"] == "amd64"
    assert promtool["prometheus_docker_architecture"] == "amd64"
    assert (output / "dr-release.json").read_bytes() == original_dr
    assert b"runner-secret" not in (output / "dr-release.json").read_bytes()
    assert promtool["alertmanager_config_sha256"] == preparer._sha256(output / "alertmanager.yml")
    assert set(promtool["source_evidence_sha256"]) == set(preparer.SOURCE_EVIDENCE_FILES)


def test_preparer_rejects_stale_or_prepopulated_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    output = tmp_path / "output"
    _write_sources(source, generated_at=datetime.now(UTC) - timedelta(hours=3))
    monkeypatch.setattr(
        preparer,
        "_run_observability_checks",
        lambda **_kwargs: (
            _validator_evidence(
                preparer.PROMETHEUS_IMAGE,
                image_id_character="b",
            ),
            _validator_evidence(
                preparer.ALERTMANAGER_IMAGE,
                image_id_character="c",
            ),
        ),
    )

    with pytest.raises(preparer.EvidencePreparationError, match="identity_"):
        preparer.prepare(
            source_dir=source,
            output_dir=output,
            git_sha=TEST_GIT_SHA,
            environment="staging",
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )

    _write_sources(tmp_path / "fresh")
    (output / "unexpected.txt").write_text("stale", encoding="utf-8")
    with pytest.raises(preparer.EvidencePreparationError, match="output_directory"):
        preparer.prepare(
            source_dir=tmp_path / "fresh",
            output_dir=output,
            git_sha=TEST_GIT_SHA,
            environment="staging",
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )


def test_observability_checks_use_pinned_container_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    config = tmp_path / "alertmanager.yml"
    config.write_text("route: {}\n", encoding="utf-8")
    commands: list[list[str]] = []

    def fake_run(command: list[str], *, step: str, timeout_seconds: float = 180.0) -> str:
        del step, timeout_seconds
        commands.append(command)
        if command[:3] == ["docker", "info", "--format"]:
            return "x86_64"
        if command[:4] == ["docker", "image", "inspect", "--format"]:
            character = "b" if "prometheus" in command[-1] else "c"
            return json.dumps(
                {
                    "Id": "sha256:" + character * 64,
                    "Os": "linux",
                    "Architecture": "amd64",
                }
            )
        return ""

    monkeypatch.setattr(preparer, "_run", fake_run)

    prometheus, alertmanager = preparer._run_observability_checks(
        alertmanager_config=config,
        prometheus_image=preparer.PROMETHEUS_IMAGE,
        alertmanager_image=preparer.ALERTMANAGER_IMAGE,
    )

    pulls = [command for command in commands if command[:2] == ["docker", "pull"]]
    runs = [command for command in commands if command[:3] == ["docker", "run", "--rm"]]
    assert len(pulls) == 2
    assert len(runs) == 3
    assert all("@sha256:" in command[-1] for command in pulls)
    assert any("/bin/promtool" in command for command in commands)
    assert any("/bin/amtool" in command for command in commands)
    assert prometheus["manifest_list_digest"].startswith("sha256:")
    assert alertmanager["manifest_list_digest"].startswith("sha256:")


@pytest.mark.parametrize(
    "prometheus_reference",
    (
        "prom/prometheus:v3.12.0",
        (
            "prom/prometheus:v3.12.0"
            "@sha256:dd4bced05dfaddf23a7ec50f87334993a4149f7fcfbf58456d1c8bafce91cd13"
        ),
    ),
)
def test_preparer_rejects_mutable_or_unapproved_single_platform_digest(
    tmp_path: Path,
    prometheus_reference: str,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    _write_sources(source)

    with pytest.raises(
        preparer.EvidencePreparationError,
        match="prometheus_image_reference",
    ):
        preparer.prepare(
            source_dir=source,
            output_dir=tmp_path / "output",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            prometheus_image=prometheus_reference,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )


def test_observability_checks_reject_cache_identity_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    config = tmp_path / "alertmanager.yml"
    config.write_text("route: {}\n", encoding="utf-8")
    monkeypatch.setattr(preparer, "_docker_architecture", lambda: "amd64")
    monkeypatch.setattr(
        preparer,
        "_pull_validator_image",
        lambda reference, **_kwargs: _validator_evidence(
            reference,
            image_id_character="b" if "prometheus" in reference else "c",
        ),
    )
    monkeypatch.setattr(preparer, "_run", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(
        preparer,
        "_image_metadata",
        lambda reference, **_kwargs: _validator_evidence(
            reference,
            image_id_character="d" if "prometheus" in reference else "c",
        ),
    )

    with pytest.raises(
        preparer.EvidencePreparationError,
        match="prometheus_image_identity_changed",
    ):
        preparer._run_observability_checks(
            alertmanager_config=config,
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )


def test_image_metadata_rejects_architecture_mismatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    preparer = _load_preparer()
    monkeypatch.setattr(
        preparer,
        "_run",
        lambda *_args, **_kwargs: json.dumps(
            {
                "Id": "sha256:" + "b" * 64,
                "Os": "linux",
                "Architecture": "arm64",
            }
        ),
    )

    with pytest.raises(preparer.EvidencePreparationError, match="prometheus_image"):
        preparer._image_metadata(
            preparer.PROMETHEUS_IMAGE,
            manifest_list_digest=preparer.PROMETHEUS_IMAGE.rsplit(
                "@",
                maxsplit=1,
            )[1],
            docker_architecture="amd64",
            step="prometheus_image",
        )


def test_preparer_rejects_inline_alertmanager_secret_before_copy(tmp_path: Path) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    _write_sources(source)
    (source / "alertmanager.yml").write_text(
        (
            "route:\n  receiver: ops\nreceivers:\n  - name: ops\n"
            "    webhook_configs:\n      - url: https://hooks.example.test/private-token\n"
        ),
        encoding="utf-8",
    )

    with pytest.raises(
        preparer.EvidencePreparationError,
        match="alertmanager_inline_secret",
    ):
        preparer.prepare(
            source_dir=source,
            output_dir=tmp_path / "output",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )


def test_preparer_rejects_sensitive_json_field_without_echoing_value(
    tmp_path: Path,
) -> None:
    preparer = _load_preparer()
    source = tmp_path / "source"
    _write_sources(source)
    evidence = json.loads((source / "dr-release.json").read_text(encoding="utf-8"))
    evidence["nested"] = {"api_key": "sk-must-never-appear"}
    (source / "dr-release.json").write_text(json.dumps(evidence), encoding="utf-8")

    with pytest.raises(
        preparer.EvidencePreparationError,
        match=r"sensitive_field_dr-release\.json",
    ) as captured:
        preparer.prepare(
            source_dir=source,
            output_dir=tmp_path / "output",
            git_sha=TEST_GIT_SHA,
            environment="staging",
            prometheus_image=preparer.PROMETHEUS_IMAGE,
            alertmanager_image=preparer.ALERTMANAGER_IMAGE,
        )

    assert "sk-must-never-appear" not in str(captured.value)
