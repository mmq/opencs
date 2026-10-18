"""The limits that bound one source address fit together (DESIGN §5)."""

from opencs import policy


def test_held_games_per_address_are_fewer_than_connections_per_address() -> None:
    assert policy.MAX_HELD_PER_ADDRESS < policy.MAX_CONNECTIONS_PER_ADDRESS


def test_an_address_can_fill_at_most_its_connections_plus_its_held_games() -> None:
    assert (
        policy.MAX_SEATS_PER_ADDRESS
        == policy.MAX_CONNECTIONS_PER_ADDRESS + policy.MAX_HELD_PER_ADDRESS
    )


def test_one_address_cannot_fill_a_city_alone() -> None:
    assert policy.MAX_SEATS_PER_ADDRESS < policy.MAX_CITY_CAPACITY
