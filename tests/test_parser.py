"""Focused cases for retroffb.parser — real NFL play descriptions, no data files."""
from __future__ import annotations

import pytest

from retroffb.models import PlayRow
from retroffb.parser import ParsedDesc, apply_to_row, estimate_air_yards, parse_desc
from retroffb.parser.air_yards import cell_keys, yards_bucket


def test_design_doc_example():
    p = parse_desc("(8:42) (Shotgun) P.Mahomes pass short right to T.Kelce to KC 45 for 9 yards (J.Smith).")
    assert (p.play_type, p.shotgun, p.no_huddle) == ("pass", True, False)
    assert (p.pass_length, p.pass_location) == ("short", "right")
    assert (p.passer, p.receiver, p.tacklers) == ("P.Mahomes", "T.Kelce", ["J.Smith"])
    assert p.complete and p.yards_gained == 9 and not p.touchdown


def test_jersey_prefixed_names_and_two_tacklers():
    p = parse_desc("(6:28) (No Huddle, Shotgun) 10-C.Rush pass short right to 40-H.Luepke to ATL 33 "
                   "for -3 yards (51-D.Malone; 97-G.Jarrett).")
    assert p.no_huddle and p.shotgun
    assert (p.passer, p.receiver) == ("C.Rush", "H.Luepke")
    assert p.tacklers == ["D.Malone", "G.Jarrett"] and p.yards_gained == -3


def test_incomplete_with_pass_defender_is_not_a_tackle():
    p = parse_desc("(2:36) (Shotgun) 18-C.Williams pass incomplete short left to 2-D.Moore [32-B.Branch].")
    assert p.play_type == "pass" and p.incomplete and not p.complete
    assert p.receiver == "D.Moore" and p.yards_gained == 0 and p.tacklers == []


def test_run_gap_mapping():
    cases = {
        "left end": ("left", "end"), "right tackle": ("right", "tackle"),
        "left guard": ("left", "guard"), "up the middle": ("middle", None),
    }
    for phrase, expected in cases.items():
        p = parse_desc(f"(11:35) 6-J.Conner {phrase} to ARI 23 for 3 yards (99-S.Tuttle, 94-A.Robinson).")
        assert p.play_type == "run" and p.rusher == "J.Conner"
        assert (p.run_location, p.run_gap) == expected
        assert p.tacklers == ["S.Tuttle", "A.Robinson"]


def test_eligible_report_does_not_become_the_rusher():
    p = parse_desc("(2:41) 70-Z.Zinter reported in as eligible. 27-D.Foreman left guard to JAX 24 "
                   "for no gain (33-D.Lloyd).")
    assert p.rusher == "D.Foreman" and p.yards_gained == 0 and p.run_gap == "guard"


def test_scramble():
    p = parse_desc("(13:17) (Shotgun) 10-D.Ridder scrambles left end ran ob at ATL 33 for 11 yards (21-M.Hughes).")
    assert p.play_type == "run" and p.qb_scramble
    assert p.rusher == "D.Ridder" and p.passer is None
    assert (p.run_location, p.run_gap, p.yards_gained) == ("left", "end", 11)


def test_kneel_and_spike():
    k = parse_desc("(:49) 14-S.Darnold kneels to SEA 45 for -1 yards.")
    assert (k.play_type, k.rusher, k.yards_gained) == ("qb_kneel", "S.Darnold", -1)
    s = parse_desc("(:56) (No Huddle) 8-D.Jones spiked the ball to stop the clock.")
    assert (s.play_type, s.passer, s.no_huddle, s.complete) == ("qb_spike", "D.Jones", True, False)


def test_sack():
    p = parse_desc("(1:03) (Shotgun) 17-D.Thompson-Robinson sacked at CLE 43 for -6 yards (58-J.Ossai).")
    assert p.play_type == "pass" and p.sack and not p.complete
    assert p.passer == "D.Thompson-Robinson" and p.yards_gained == -6 and p.tacklers == ["J.Ossai"]


def test_sack_fumble_lost():
    p = parse_desc("(12:37) (Shotgun) 17-B.Allen sacked at SF 9 for -6 yards (90-L.Van Ness). FUMBLES "
                   "(90-L.Van Ness) [90-L.Van Ness], RECOVERED by GB-97-K.Clark at SF 16.")
    assert p.sack and p.fumble and p.fumble_lost
    assert p.fumbler == "B.Allen" and p.fumble_recovery_team == "GB"
    assert p.tacklers == ["L.Van Ness"]


def test_fumble_recovered_by_own_team_is_not_lost():
    p = parse_desc("(4:10) (No Huddle, Shotgun) 7-G.Smith pass short right to 11-J.Smith-Njigba to SF 32 for "
                   "7 yards (54-F.Warner). FUMBLES (54-F.Warner), recovered by SEA-75-A.Bradford at SF 38.")
    assert p.fumble and not p.fumble_lost and p.receiver == "J.Smith-Njigba"
    assert p.fumbler == "J.Smith-Njigba"
    assert p.yards_gained == 1  # charged back to the recovery spot, as the stat feed does
    q = parse_desc("(11:21) (Shotgun) 18-T.Huntley sacked at NYJ 48 for -5 yards. FUMBLES, and recovers at NYJ 48.")
    assert q.fumble and not q.fumble_lost and q.yards_gained == -5


def test_pick_six():
    p = parse_desc("(3:10) (Shotgun) 9-B.Young pass short left intended for 17-X.Legette INTERCEPTED by "
                   "29-J.Pitre at CAR 35. 29-J.Pitre for 35 yards, TOUCHDOWN.")
    assert p.play_type == "pass" and p.interception and not p.complete and not p.incomplete
    assert (p.passer, p.receiver, p.interceptor) == ("B.Young", "X.Legette", "J.Pitre")
    assert p.touchdown and p.td_scorer == "J.Pitre"
    assert p.yards_gained == 0 and p.return_yards == 35
    row, _ = apply_to_row(p, {"posteam": "CAR", "defteam": "HOU"})
    assert row["td_team"] == "HOU"


def test_reversal_after_text_is_truth():
    p = parse_desc("(10:51) (Shotgun) 7-G.Smith pass deep right to 14-D.Metcalf for 30 yards, TOUCHDOWN. The "
                   "Replay Official reviewed the runner broke the plane ruling, and the play was REVERSED. "
                   "(Shotgun) 7-G.Smith pass deep right to 14-D.Metcalf to DET 1 for 29 yards (12-B.Joseph).")
    assert p.reversed and not p.touchdown and p.td_scorer is None
    assert p.complete and p.yards_gained == 29 and p.pass_length == "deep" and p.tacklers == ["B.Joseph"]


def test_reversal_completion_to_incomplete():
    p = parse_desc("(10:16) 10-J.Herbert pass short right to 1-Q.Johnston to HOU 33 for 1 yard (24-D.Stingley). "
                   "The Replay Official reviewed the pass completion ruling, and the play was REVERSED. "
                   "10-J.Herbert pass incomplete short right to 1-Q.Johnston (24-D.Stingley).")
    assert p.reversed and p.incomplete and not p.complete and p.yards_gained == 0 and p.tacklers == []


def test_upheld_review_changes_nothing():
    p = parse_desc("(7:57) 22-D.Henry right end for 4 yards, TOUCHDOWN. The Replay Official reviewed the runner "
                   "broke the plane ruling, and the play was Upheld. The ruling on the field stands.")
    assert not p.reversed and p.touchdown and p.td_scorer == "D.Henry" and p.yards_gained == 4


def test_lateral_touchdown_scorer_and_total_yards():
    p = parse_desc("(6:05) 70-A.Anderson reported in as eligible. 17-J.Allen pass short left to 18-A.Cooper to "
                   "SF 9 for -2 yards. Lateral to 17-J.Allen for 9 yards, TOUCHDOWN.")
    assert p.lateral and p.touchdown and p.complete
    assert (p.passer, p.receiver, p.td_scorer, p.yards_gained) == ("J.Allen", "A.Cooper", "J.Allen", 7)


def test_penalty_no_play():
    p = parse_desc("(1:54) (Shotgun) 15-G.Minshew pass incomplete short right. PENALTY on LV-8-A.Abdullah, "
                   "Offensive Pass Interference, 10 yards, enforced at LV 33 - No Play.")
    assert p.play_type == "no_play" and p.penalty and p.penalty_nullified
    assert p.nullified_play_type == "pass" and p.shotgun
    assert (p.penalty_team, p.penalty_type) == ("LV", "Offensive Pass Interference")
    assert p.passer is None and p.yards_gained == 0 and not p.incomplete


def test_presnap_penalty_and_nullified_touchdown():
    p = parse_desc("(12:41) (Shotgun) PENALTY on KC-64-W.Morris, False Start, 5 yards, enforced at LV 30 - No Play.")
    assert p.play_type == "no_play" and p.penalty and p.penalty_type == "False Start"
    q = parse_desc("(8:04) 39-H.To'oTo'o for 38 yards, TOUCHDOWN NULLIFIED by Penalty. PENALTY on HOU-95-D.Barnett, "
                   "Roughing the Passer, 15 yards, enforced at KC 41 - No Play.")
    assert q.play_type == "no_play" and not q.touchdown


def test_accepted_penalty_that_does_not_nullify():
    p = parse_desc("(13:41) (Shotgun) 7-J.Brissett pass short left to 38-R.Stevenson to NE 44 for 6 yards "
                   "(91-E.Ogbah). PENALTY on MIA-5-J.Ramsey, Roughing the Passer, 15 yards, enforced at NE 44.")
    assert p.play_type == "pass" and p.penalty and not p.penalty_nullified
    assert p.complete and p.yards_gained == 6 and p.receiver == "R.Stevenson" and p.tacklers == ["E.Ogbah"]


def test_declined_penalty_is_not_a_penalty():
    p = parse_desc("(4:50) 33-J.Williams left tackle to CAR 49 for 2 yards (47-J.Jewell). "
                   "Penalty on DEN, Illegal Formation, declined.")
    assert p.play_type == "run" and not p.penalty and p.yards_gained == 2


def test_offensive_spot_foul_trims_the_gain():
    p = parse_desc("(15:00) (Shotgun) 4-J.Cook left end to BUF 45 for 5 yards (7-K.White, 3-B.Baker). PENALTY on "
                   "BUF-73-D.Dawkins, Offensive Holding, 10 yards, enforced at BUF 41. Rush credited to BUF 41.")
    assert p.play_type == "run" and p.penalty and p.yards_gained == 1


def test_two_point_pass_and_run():
    ok = parse_desc("TWO-POINT CONVERSION ATTEMPT. 2-T.Taylor pass to 83-T.Conklin is complete. ATTEMPT SUCCEEDS.")
    assert ok.two_point_attempt and ok.two_point_result == "success" and ok.play_type == "pass"
    assert (ok.passer, ok.receiver) == ("T.Taylor", "T.Conklin") and not ok.touchdown
    bad = parse_desc("TWO-POINT CONVERSION ATTEMPT. 28-J.Mixon rushes up the middle. ATTEMPT FAILS.")
    assert bad.two_point_result == "failure" and bad.play_type == "run" and bad.rusher == "J.Mixon"
    row, _ = apply_to_row(ok, {})
    assert row["two_point"] == "success"


def test_two_point_defensive_return_does_not_flip_result():
    p = parse_desc("TWO-POINT CONVERSION ATTEMPT. 6-T.Shough pass to 14-D.Vele is intercepted. ATTEMPT FAILS. "
                   "DEFENSIVE TWO-POINT ATTEMPT. 29-M.Fitzpatrick intercepted the try attempt. ATTEMPT SUCCEEDS.")
    assert p.two_point_result == "failure"


def test_field_goals():
    good = parse_desc("(1:11) 41-A.Carlson 55 yard field goal is GOOD, Center-46-T.Pepper, Holder-3-M.Wishnowsky.")
    assert (good.play_type, good.field_goal_result, good.kick_distance, good.kicker) == (
        "field_goal", "made", 55, "A.Carlson")
    miss = parse_desc("(12:48) 6-Y.Koo 54 yard field goal is No Good, Wide Left, Center-49-L.McCullough, "
                      "Holder-13-B.Pinion.")
    assert miss.field_goal_result == "missed" and miss.kick_distance == 54


def test_blocked_field_goal_returned_for_touchdown():
    p = parse_desc("(:04) 3-C.Santos 46 yard field goal is BLOCKED (94-K.Brooks), Center-46-S.Daly, "
                   "Holder-19-T.Taylor, RECOVERED by GB-25-K.Nixon at GB 40. 25-K.Nixon for 60 yards, TOUCHDOWN.")
    assert p.play_type == "field_goal" and p.field_goal_result == "blocked"
    assert p.kicker == "C.Santos" and p.kick_distance == 46
    assert p.touchdown and p.td_scorer == "K.Nixon"


def test_extra_points():
    good = parse_desc("2-E.McPherson extra point is GOOD, Center-48-C.Adomitis, Holder-8-R.Rehkow.")
    assert (good.play_type, good.extra_point_result, good.kicker) == ("extra_point", "good", "E.McPherson")
    bad = parse_desc("96-J.Romo extra point is No Good, Hit Right Upright, Center-47-J.McQuaide, Holder-17-R.Wright.")
    assert bad.extra_point_result == "failed"
    blk = parse_desc("9-G.Zuerlein extra point is Blocked (39-M.Fitzpatrick), Center-42-T.Hennessy, "
                     "Holder-6-T.Morstead.")
    assert blk.extra_point_result == "blocked"


def test_touchback_kickoff_and_return():
    tb = parse_desc("16-T.Gill kicks 65 yards from TB 35 to end zone, Touchback to the ATL 30.")
    assert (tb.play_type, tb.kicker, tb.kick_distance, tb.touchback, tb.returner) == (
        "kickoff", "T.Gill", 65, True, None)
    ret = parse_desc("9-C.Boswell kicks 60 yards from PIT 35 to NYJ 5. 32-I.Davis to NYJ 26 for 21 yards "
                     "(83-Co.Heyward; 38-T.Edmunds).")
    assert ret.returner == "I.Davis" and ret.return_yards == 21 and ret.yards_gained == 0
    assert ret.tacklers == ["Co.Heyward", "T.Edmunds"]


def test_punts():
    fc = parse_desc("(11:52) 16-J.Scott punts 39 yards to CIN 14, Center-47-J.Harris, fair catch by 16-T.Irwin.")
    assert (fc.play_type, fc.punter, fc.kick_distance, fc.returner, fc.return_yards) == (
        "punt", "J.Scott", 39, "T.Irwin", 0)
    muff = parse_desc("(11:34) 6-T.Townsend punts 42 yards to TEN 39, Center-46-J.Weeks. 19-J.Jackson MUFFS catch, "
                      "RECOVERED by HOU-17-K.Boyd at TEN 43.")
    assert muff.fumble and muff.fumble_lost and muff.returner == "J.Jackson"


@pytest.mark.parametrize(
    ("text", "name"),
    [
        ("(3:12) 16-J.Goff pass short middle to 14-A.St. Brown to DET 40 for 8 yards (2-J.Doe).", "A.St. Brown"),
        ("(3:12) J.Goff pass short middle to Am.St. Brown to DET 40 for 8 yards (J.Doe).", "Am.St. Brown"),
        ("(3:12) G.Smith pass deep left to D.K. Metcalf for 35 yards, TOUCHDOWN.", "D.K.Metcalf"),
        ("(3:12) K.Murray pass short right to M.Harrison Jr. to ARI 30 for 5 yards (J.Doe).", "M.Harrison Jr."),
        ("(3:12) 1-K.Murray pass short right to 18-M.Harrison Jr. pushed ob at ARI 30 for 5 yards.", "M.Harrison Jr."),
        ("(3:12) 1-J.Hurts pass short left to 6-De.Smith to PHI 30 for 5 yards (J.Doe).", "De.Smith"),
        ("(3:12) 9-J.Burrow pass deep right to 1-Ja'M.Chase for 40 yards, TOUCHDOWN.", "Ja'M.Chase"),
        ("(3:12) 7-G.Smith pass short left to 11-J.Smith-Njigba to SEA 30 for 5 yards (J.Doe).", "J.Smith-Njigba"),
        ("(3:12) 8-X.Yz pass short left to 39-H.To'oTo'o to SEA 30 for 5 yards (90-L.Van Ness).", "H.To'oTo'o"),
    ],
)
def test_name_shapes(text: str, name: str):
    p = parse_desc(text)
    assert p.receiver == name
    assert p.complete and p.yards_gained in (5, 8, 35, 40)


def test_st_brown_touchdown_scorer():
    p = parse_desc("(:22) (Shotgun) 16-J.Goff pass short right to 14-A.St. Brown for 7 yards, TOUCHDOWN.")
    assert p.touchdown and p.td_scorer == "A.St. Brown" and p.passer == "J.Goff"


def test_safety():
    p = parse_desc("(4:36) (Shotgun) 26-Z.Charbonnet right guard tackled in End Zone for -1 yards, SAFETY "
                   "(99-Z.Allen, 0-J.Cooper).")
    assert p.safety and p.play_type == "run" and p.yards_gained == -1
    assert p.run_gap == "guard" and p.tacklers == ["Z.Allen", "J.Cooper"]


def test_bobbled_snap_then_pass_is_a_pass():
    p = parse_desc("(9:02) (Shotgun) 7-G.Smith to SEA 26 for -5 yards. FUMBLES, and recovers at SEA 25. "
                   "7-G.Smith pass short right to 87-N.Fant to SEA 35 for 4 yards (8-J.Holland).")
    assert p.play_type == "pass" and p.complete and p.fumble and not p.fumble_lost
    assert (p.passer, p.receiver, p.rusher, p.yards_gained) == ("G.Smith", "N.Fant", None, 4)


def test_administrative_lines_and_timeouts():
    assert parse_desc("END QUARTER 2").play_type is None
    assert parse_desc("GAME").play_type is None
    assert parse_desc("Timeout #2 by CIN at 00:43.").play_type == "no_play"


@pytest.mark.parametrize("junk", ["", "   ", "???", "(((", "pass", "12-", None, 42, "TOUCHDOWN " * 50,
                                  "REVERSED.", "PENALTY on", "kicks", ") ( [ ]", "\x00\n\t"])
def test_never_raises(junk):
    p = parse_desc(junk)  # type: ignore[arg-type]
    assert isinstance(p, ParsedDesc)
    assert p.tacklers == [] and not p.touchdown


def test_apply_to_row_builds_a_playrow():
    p = parse_desc("(8:42) (Shotgun) 15-P.Mahomes pass short right to 87-T.Kelce to KC 45 for 9 yards (54-F.Warner).")
    base = dict(play_id="g:1", game_id="g", seq=1, sim_time=0.0, quarter=1, clock="8:42", down=2, ydstogo=7,
                yardline_100=64, posteam="KC", defteam="SF", desc="...")
    row, names = apply_to_row(p, base)
    play = PlayRow(**row)
    assert play.play_type == "pass" and play.complete and play.shotgun and play.first_down
    assert play.yards_gained == 9 and play.passer_id is None
    assert names["passer_name"] == "P.Mahomes" and names["receiver_name"] == "T.Kelce"
    assert names["tackler_names"] == ["F.Warner"]


def test_apply_to_row_prefers_delivered_yards():
    p = parse_desc("(8:42) 22-D.Henry left end to BAL 30 for 5 yards (54-F.Warner).")
    row, _ = apply_to_row(p, {"yards_gained": 4, "ydstogo": 10})
    assert row["yards_gained"] == 4 and row["first_down"] is False


def test_air_yards_prior():
    short = estimate_air_yards("short", "right", 9, 2, 7, True)
    deep = estimate_air_yards("deep", "left", 0, 3, 12, False)
    assert 0 <= short <= 14 and deep >= 15
    # behind-the-line screens that went nowhere have little air under them
    assert estimate_air_yards("short", "left", -2, 1, 10, True) <= 1
    # red zone: cannot be thrown past the back of the end zone
    assert estimate_air_yards("deep", "middle", 0, 1, 10, False, yardline_100=8) <= 17
    # unknown everything still answers
    assert isinstance(estimate_air_yards(None, None, None, None, None, False), float)
    assert yards_bucket(33, True) == "31-40" and yards_bucket(33, False) == "na"
    assert len(cell_keys("short", "left", 5, 1, 10, True)) == 9
