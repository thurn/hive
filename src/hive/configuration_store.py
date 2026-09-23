"""Administrative changes share admission exclusion with worker decisions."""

import json
from dataclasses import dataclass

from hive.bead_json import decode_bead
from hive.beads_store import BeadsStore
from hive.configuration import (
    Configuration,
    Project,
    configuration_metadata,
    decode_configuration,
    find_configuration,
)
from hive.errors import ErrorCode, HiveError
from hive.jsonvalue import record, sequence, string
from hive.locking import Guards
from hive.model import Capacity


@dataclass(frozen=True)
class ConfigurationStore:
    store: BeadsStore
    guards: Guards

    def read(self) -> Configuration:
        return find_configuration(self.store.active_records())

    def initialize(self, projects: tuple[Project, ...] = ()) -> Configuration:
        with self.guards.mutation(), self.guards.admission():
            prefix = record(self.store.process.run(["config", "get", "issue_prefix"]))
            if prefix.get("value") != "hv":
                raise HiveError(
                    ErrorCode.INVALID_INPUT,
                    "Refusing to initialize a non-Hive database",
                )
            records = self.store.active_records()
            if records:
                # A lost creation response can be recovered without duplicating
                # configuration or adopting arbitrary existing task ownership.
                return find_configuration(records)
            types = record(self.store.process.run(["config", "get", "types.custom"]))
            current = string(types.get("value"), "custom types", empty=True)
            values = tuple(part for part in current.split(",") if part)
            if "role" not in values:
                self.store.process.run(
                    ["config", "set", "types.custom", ",".join((*values, "role"))],
                    mutation=True,
                )
            metadata = configuration_metadata(projects, Capacity())
            result = self.store.process.run(
                [
                    "create",
                    "--type",
                    "role",
                    "--no-history",
                    "--title",
                    "Hive configuration",
                    "--description",
                    "Project registration and global admission limits",
                    "--metadata",
                    json.dumps(metadata),
                ],
                mutation=True,
            )
            try:
                return decode_configuration(result)
            except HiveError as error:
                raise HiveError(error.code, error.detail, uncertain=True) from error

    def replace(
        self, projects: tuple[Project, ...], capacity: Capacity
    ) -> Configuration:
        with self.guards.mutation(), self.guards.admission():
            records = self.store.active_records()
            old = find_configuration(records)
            for raw in records:
                if raw.get("issue_type") in {"role", "agent", "message"}:
                    continue
                bead = decode_bead(raw)
                previous = old.project(bead.project)
                project = next((p for p in projects if p.id == bead.project), None)
                if project is None or (
                    project.repository != previous.repository
                    or project.native_id != previous.native_id
                ):
                    raise HiveError(
                        ErrorCode.INVALID_INPUT,
                        "Finish or reconcile unfinished project work before "
                        "changing its execution binding",
                    )
            metadata = configuration_metadata(projects, capacity)
            # Validate the proposed value before changing the native record.
            expected = decode_configuration(
                {
                    "id": old.id,
                    "issue_type": "role",
                    "status": "open",
                    "no_history": True,
                    "metadata": metadata,
                }
            )
            value = self.store.process.run(
                ["update", old.id, "--metadata", json.dumps(metadata)], mutation=True
            )
            try:
                results = sequence(value, "updated configuration")
                if len(results) != 1 or decode_configuration(results[0]) != expected:
                    raise HiveError(
                        ErrorCode.INVALID_RECORD,
                        "Configuration update was not acknowledged",
                    )
            except HiveError as error:
                raise HiveError(error.code, error.detail, uncertain=True) from error
            return expected
