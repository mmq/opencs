import logging

import pytest

from opencs.connlog import ConnectionContext, ConnectionLog


def test_records_carry_the_connection_id_and_the_callsign_once_known(
    caplog: pytest.LogCaptureFixture,
) -> None:
    context = ConnectionContext(7)
    net = ConnectionLog(logging.getLogger("opencs.net"), context)
    session = ConnectionLog(logging.getLogger("opencs.session"), context)
    with caplog.at_level(logging.INFO):
        net.info("accepted")
        session.callsign = "ALPHA"
        net.info("closed")
    assert [r.getMessage() for r in caplog.records] == [
        "[conn=7 -] accepted",
        "[conn=7 ALPHA] closed",  # the net layer sees the callsign the session learned
    ]
    assert [r.name for r in caplog.records] == ["opencs.net", "opencs.net"]
