from datetime import datetime, time, timedelta

from planner.scheduler import (Interval, RoutineSpec, TaskSpec, build_free_time,
                               effective_due, plan)

MON = datetime(2026, 10, 19)  # a Monday


def at(day_offset, h, m=0):
    return MON + timedelta(days=day_offset, hours=h, minutes=m)


def task(id, title, minutes, due, **kw):
    kw.setdefault("effective_due", due)
    return TaskSpec(id=id, title=title, remaining=minutes, due=due, **kw)


def run(tasks, busy=(), routines=(), now=None, days=7):
    now = now or at(0, 8)
    return plan(now, now + timedelta(days=days), time(8), time(23), list(busy), list(routines), tasks)


def task_minutes(result, tid):
    return sum((b.end - b.start).seconds // 60 for b in result.blocks if b.task_id == tid)


def test_free_time_respects_day_window_and_busy():
    free = build_free_time(at(0, 8), at(1, 0), time(8), time(23), [Interval(at(0, 10), at(0, 12))])
    assert [(f.start.hour, f.end.hour) for f in free] == [(8, 10), (12, 23)]


def test_task_scheduled_fully_before_deadline_and_not_in_busy_time():
    busy = [Interval(at(0, 8), at(0, 22))]
    r = run([task(1, "CPS", 180, at(1, 23, 59))], busy=busy)
    assert task_minutes(r, 1) == 180
    for b in r.blocks:
        assert not (b.start < at(0, 22) and b.end > at(0, 8))
        assert b.end <= at(1, 23, 59)
    assert not [w for w in r.warnings if w.level == "danger"]


def test_edf_orders_by_deadline():
    r = run([task(1, "later", 60, at(3, 12)), task(2, "sooner", 60, at(1, 12))])
    first = min(r.blocks, key=lambda b: b.start)
    assert first.task_id == 2


def test_blocks_respect_max_and_min_block():
    r = run([task(1, "big", 400, at(2, 12), max_block=120, min_block=30)])
    lens = [(b.end - b.start).seconds // 60 for b in r.blocks]
    assert sum(lens) == 400 and max(lens) <= 120 and min(lens) >= 30


def test_non_splittable_needs_one_contiguous_slot():
    # Only 60-min gaps today; a 90-min unsplittable task must go to tomorrow.
    busy = [Interval(at(0, 9), at(0, 10)), Interval(at(0, 11), at(0, 23))]
    r = run([task(1, "exam", 90, at(2, 12), splittable=False)], busy=busy)
    (b,) = [b for b in r.blocks if b.task_id == 1]
    assert b.start.date() == at(1, 0).date() and (b.end - b.start).seconds == 90 * 60


def test_routine_placed_in_its_window_and_tasks_avoid_it():
    lunch = RoutineSpec(1, "Lunch", "M", time(12), time(13, 45), 45)
    r = run([task(1, "work", 15 * 60, at(0, 23))], routines=[lunch], days=1)
    (l,) = [b for b in r.blocks if b.routine_id == 1]
    assert l.start == at(0, 12) and l.end == at(0, 12, 45)
    for b in r.blocks:
        if b.task_id:
            assert b.end <= l.start or b.start >= l.end


def test_routine_whose_window_passed_is_skipped():
    breakfast = RoutineSpec(1, "Breakfast", "M", time(8), time(10), 30)
    r = run([], routines=[breakfast], now=at(0, 11), days=1)
    assert not r.blocks


def test_compression_when_overbooked():
    # 4 free hours, 5 hours of work; task 2 can be squeezed to 2h.
    busy = [Interval(at(0, 12), at(0, 23))]
    tasks = [task(1, "A", 180, at(0, 23)), task(2, "B", 120 + 60, at(0, 23), min_total=60)]
    r = run(tasks, busy=busy, days=1)
    assert r.compressed == {2: 120}
    assert task_minutes(r, 1) == 180 and task_minutes(r, 2) == 60
    assert not r.shortfall


def test_shortfall_reported_when_impossible():
    busy = [Interval(at(0, 9), at(0, 23))]
    r = run([task(1, "A", 180, at(0, 23))], busy=busy, days=1)
    assert r.shortfall == {1: 120}
    assert any(w.level == "danger" and "120 min short" in w.message for w in r.warnings)


def test_soft_deadline_spills_to_real_deadline_with_warning():
    # Rule says done by Wed; Wed is full, so the rest spills to Thu before the real Fri deadline.
    busy = [Interval(at(0, 8), at(2, 23))]
    t = task(1, "M3", 120, at(4, 11), effective_due=at(2, 23, 59))
    r = run([t], busy=busy)
    assert task_minutes(r, 1) == 120
    assert any("misses its target" in w.message for w in r.warnings)


def test_past_due_task_flagged():
    r = run([task(1, "late", 60, at(0, 7))])
    assert any(w.level == "danger" and "past due" in w.message for w in r.warnings)


def test_effective_due_rule_moves_to_previous_wednesday():
    rules = [{"tag": "db", "weekday": 2, "time": time(23, 59)}]
    fri_11am = datetime(2026, 10, 23, 11)
    assert effective_due(fri_11am, ["db"], rules) == datetime(2026, 10, 21, 23, 59)
    # Untagged work keeps its real deadline.
    assert effective_due(fri_11am, ["plc"], rules) == fri_11am
    # Due before the rule's day/time that week -> unchanged.
    wed_noon = datetime(2026, 10, 21, 12)
    assert effective_due(wed_noon, ["db"], rules) == wed_noon


def test_sequential_subtasks_run_in_order():
    due = at(3, 23)
    subs = [task(10 + i, f"step{i}", 60, due, group=1, seq=i, sequential=True, max_block=60) for i in range(3)]
    # Give the last step the most remaining-time tie-break advantage; order must still hold.
    r = run(list(reversed(subs)))
    ends = {tid: max(b.end for b in r.blocks if b.task_id == tid) for tid in (10, 11, 12)}
    starts = {tid: min(b.start for b in r.blocks if b.task_id == tid) for tid in (10, 11, 12)}
    assert ends[10] <= starts[11] and ends[11] <= starts[12]


def test_unordered_subtasks_can_interleave():
    due = at(3, 23)
    subs = [task(20 + i, f"part{i}", 60, due, group=2, seq=i, sequential=False) for i in range(2)]
    r = run(subs)
    assert task_minutes(r, 20) == 60 and task_minutes(r, 21) == 60
