from sqlalchemy import select

from app.bootstrap import remove_retired_repair_app
from app.database import SessionLocal
from app.models import AuditLog, ManagedApplication


def test_remove_retired_repair_app_deletes_seed_and_unit(client, monkeypatch):
    from app.applications import systemd as sd

    calls: list[tuple[str, str]] = []
    monkeypatch.setattr(sd, "stop", lambda unit: calls.append(("stop", unit)) or (True, ""))
    monkeypatch.setattr(sd, "remove_unit", lambda unit: calls.append(("remove", unit)))

    with SessionLocal() as db:
        app = ManagedApplication(
            name="Claude 修復コンソール",
            description="legacy seed",
            application_type="shell_script",
            script_path="/old/ControlDeck/scripts/claude-repair.sh",
            working_directory="/old/ControlDeck",
            arguments_json="[]",
            restart_policy="no",
            systemd_unit_name="cdapp-999.service",
        )
        db.add(app)
        db.commit()
        app_id = app.id

        assert remove_retired_repair_app(db) == 1
        assert db.get(ManagedApplication, app_id) is None
        entry = db.execute(
            select(AuditLog).where(
                AuditLog.action == "app.retired_remove",
                AuditLog.resource_id == str(app_id),
            )
        ).scalar_one()
        assert entry.resource_type == "app"

    assert calls == [("stop", "cdapp-999.service"), ("remove", "cdapp-999.service")]
