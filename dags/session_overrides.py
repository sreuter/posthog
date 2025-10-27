from dataclasses import dataclass

from clickhouse_driver import Client

from posthog import settings
from posthog.models.event.sql import EVENTS_DATA_TABLE
from posthog.models.person.sql import PERSON_DISTINCT_ID_OVERRIDES_TABLE

from .common.overrides_manager import OverridesSnapshotTable


@dataclass
class SessionOverridesSnapshotTable(OverridesSnapshotTable):
    @property
    def name(self) -> str:
        return f"session_overrides_snapshot_{self.id.hex}"

    def create(self, client: Client) -> None:
        client.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {self.qualified_name} (team_id Int64, distinct_id String, person_id UUID, version Int64)
            ENGINE = ReplicatedReplacingMergeTree('/clickhouse/tables/noshard/{self.qualified_name}', '{{replica}}-{{shard}}', version)
            ORDER BY (team_id, distinct_id)
            """
        )

    def populate(self, client: Client, timestamp: str, limit: int | None = None) -> None:
        # NOTE: this is theoretically subject to replication lag and accuracy of this result is not a guarantee
        # this could optionally support truncate as a config option if necessary to reset the table state, or
        # force an optimize after insertion to compact the table before dictionary insertion (if that's even needed)
        [[count]] = client.execute(f"SELECT count() FROM {self.qualified_name}")
        assert count == 0

        limit_clause = f"LIMIT {limit}" if limit else ""

        client.execute(
            f"""
            INSERT INTO {self.qualified_name} (team_id, distinct_id, person_id, version)
            SELECT team_id, distinct_id, argMax(person_id, version), max(version)
            FROM {settings.CLICKHOUSE_DATABASE}.{PERSON_DISTINCT_ID_OVERRIDES_TABLE}
            WHERE _timestamp < %(timestamp)s
            GROUP BY team_id, distinct_id
            {limit_clause}
            """,
            {"timestamp": timestamp},
            settings={
                "optimize_aggregation_in_order": 1,  # slows down the query, but reduces memory consumption dramatically
            },
        )


@dataclass
class SessionOverridesSnapshotDictionary(SessionOverridesSnapshotTable):
    @property
    def alter_table_name(self) -> str:
        return EVENTS_DATA_TABLE()

    @property
    def overrides_delete_table_name(self) -> str:
        return PERSON_DISTINCT_ID_OVERRIDES_TABLE

    def create(self, client: Client, shards: int, max_execution_time: int, max_memory_usage: int) -> None:
        client.execute(
            f"""
            CREATE DICTIONARY IF NOT EXISTS {self.qualified_name} (
                team_id Int64,
                distinct_id String,
                person_id UUID,
                version Int64
            )
            PRIMARY KEY team_id, distinct_id
            SOURCE(CLICKHOUSE(DB %(database)s TABLE %(table)s USER %(user)s PASSWORD %(password)s))
            LAYOUT(COMPLEX_KEY_HASHED(SHARDS {shards}))
            LIFETIME(0)
            SETTINGS(max_execution_time={max_execution_time}, max_memory_usage={max_memory_usage})
            """,
            {
                "database": settings.CLICKHOUSE_DATABASE,
                "table": self.source.name,
                "user": settings.CLICKHOUSE_USER,
                "password": settings.CLICKHOUSE_PASSWORD,
            },
        )

    @property
    def update_commands(self) -> set[str]:
        return {
            "UPDATE person_id = dictGet(%(name)s, 'person_id', (team_id, distinct_id)) WHERE dictHas(%(name)s, (team_id, distinct_id))"
        }

    @property
    def deletes_predicate(self) -> str:
        return "isNotNull(dictGetOrNull(%(name)s, 'version', (team_id, distinct_id)) as snapshot_version) AND snapshot_version >= version"
