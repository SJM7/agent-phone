"""Independent acceptance suite for attention.py."""
from agent_phone.attention import AttentionRouter


def make(*keys):
    r = AttentionRouter()
    for k in keys:
        r.bind(k, f"label-{k}")
    return r


def test_bind_rebind_preserves_order_updates_label():
    r = make("a", "b", "c")
    r.bind("a", "new-label")
    assert r.bindings() == [("a", "new-label"), ("b", "label-b"), ("c", "label-c")]


def test_bindings_and_needs_attention_return_fresh_lists():
    r = make("a")
    r.mark_attention("a")
    b1, b2 = r.bindings(), r.bindings()
    assert b1 == b2 and b1 is not b2
    n1, n2 = r.needs_attention(), r.needs_attention()
    assert n1 == n2 and n1 is not n2
    n1.append("junk")
    assert r.needs_attention() == ["a"]


def test_mark_attention_rules():
    r = make("a", "b")
    assert r.mark_attention("nope") is False
    assert r.mark_attention("a") is True
    assert r.mark_attention("a") is False          # already queued
    assert r.mark_attention("b") is True
    assert r.needs_attention() == ["a", "b"]


def test_clear_attention_rules():
    r = make("a", "b")
    assert r.clear_attention("a") is False         # not queued
    r.mark_attention("a")
    assert r.clear_attention("a") is True
    assert r.needs_attention() == []
    assert r.clear_attention("a") is False


def test_round_robin_three_keys():
    r = make("a", "b", "c")
    for k in ("a", "b", "c"):
        r.mark_attention(k)
    got = [r.next_attention() for _ in range(7)]
    assert got == ["a", "b", "c", "a", "b", "c", "a"]
    assert r.current() == "a"


def test_single_key_repeats():
    r = make("only")
    r.mark_attention("only")
    assert [r.next_attention() for _ in range(3)] == ["only"] * 3


def test_empty_queue():
    r = make("a")
    assert r.next_attention() is None
    assert r.current() is None


def test_clearing_current_resets_cursor():
    r = make("a", "b")
    r.mark_attention("a")
    r.mark_attention("b")
    assert r.next_attention() == "a"
    r.clear_attention("a")
    assert r.current() is None
    assert r.next_attention() == "b"               # restarts at front


def test_clearing_non_current_keeps_cycle_going():
    r = make("a", "b", "c")
    for k in ("a", "b", "c"):
        r.mark_attention(k)
    assert r.next_attention() == "a"
    r.clear_attention("b")                          # non-current removed
    assert r.current() == "a"
    assert r.next_attention() == "c"                # entry after cursor, skipping b
    assert r.next_attention() == "a"                # wraps


def test_unbind_prunes_queue_and_cursor():
    r = make("a", "b")
    r.mark_attention("a")
    r.mark_attention("b")
    assert r.next_attention() == "a"
    assert r.unbind("a") is True
    assert r.current() is None
    assert r.needs_attention() == ["b"]
    assert r.next_attention() == "b"
    assert r.unbind("ghost") is False
    assert r.mark_attention("a") is False           # no longer bound


def test_new_mark_during_cycle_lands_at_queue_end():
    r = make("a", "b", "c")
    r.mark_attention("a")
    r.mark_attention("b")
    assert r.next_attention() == "a"
    r.mark_attention("c")
    assert r.next_attention() == "b"
    assert r.next_attention() == "c"
    assert r.next_attention() == "a"


def test_instances_independent():
    r1, r2 = make("a"), make("a")
    r1.mark_attention("a")
    assert r2.needs_attention() == []
