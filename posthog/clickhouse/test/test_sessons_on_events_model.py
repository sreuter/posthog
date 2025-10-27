
from posthog.test.base import (
    BaseTest,
    ClickhouseDestroyTablesMixin,
    ClickhouseTestMixin,
    _create_event,
    flush_persons_and_events,
)

from posthog.clickhouse.client import query_with_columns, sync_execute
from posthog.models.raw_sessions.sessions_on_events_overrides import RAW_SESSION_OVERRIDES_SQUASH_COPY_SQL
from posthog.models.utils import uuid7

distinct_id_counter = 0
session_id_counter = 0


def create_distinct_id():
    global distinct_id_counter
    distinct_id_counter += 1
    return f"d{distinct_id_counter}"


def create_session_id():
    global session_id_counter
    session_id_counter += 1
    return str(uuid7(random=session_id_counter))


class TestSessionsOnEventsModel(ClickhouseDestroyTablesMixin, ClickhouseTestMixin, BaseTest):
    snapshot_replace_all_numbers = True

    def select_by_session_id(self, session_id):
        flush_persons_and_events()
        return query_with_columns(
            """
            select
                *
            from raw_sessions_v3_v
            where
                session_id_v7 = toUInt128(toUUID(%(session_id)s)) AND
                team_id = %(team_id)s
                """,
            {
                "session_id": session_id,
                "team_id": self.team.id,
            },
        )

    def test_it_creates_session_overrides_entry_when_creating_event(self):
        distinct_id = create_distinct_id()
        session_id = create_session_id()
        _create_event(
            team=self.team,
            event="$pageview",
            distinct_id=distinct_id,
            properties={"$current_url": "/", "$session_id": session_id},
            timestamp="2024-03-08",
        )

        response = sync_execute(
            """
            select
                session_id_v7,
                team_id
            from raw_sessions_overrides_v3
            where
                session_id_v7 = toUInt128(toUUID(%(session_id)s))  AND
                team_id = %(team_id)s
                """,
            {
                "session_id": session_id,
                "team_id": self.team.id,
            },
        )

        assert len(response) == 1

    def test_it_copies_property_to_events_table_in_squash_job(self):
        distinct_id = create_distinct_id()
        session_id = create_session_id()
        _create_event(
            team=self.team,
            event="$pageview",
            distinct_id=distinct_id,
            properties={"$current_url": "/", "$session_id": session_id},
            timestamp="2024-03-08",
        )

        squash_sql =RAW_SESSION_OVERRIDES_SQUASH_COPY_SQL(squash_before="'2100-01-01'", where=f"team_id = {self.team.id}", start_date="'1970-01-01'", end_date="'2100-01-01'")
        sync_execute(squash_sql)

        response = sync_execute(
            """
            select
                *
            from events
            where
                session_id_uuid = toUInt128(toUUID(%(session_id)s))  AND
                team_id = %(team_id)s
                """,
            {
                "session_id": session_id,
                "team_id": self.team.id,
            },
        )

        assert len(response) == 1
