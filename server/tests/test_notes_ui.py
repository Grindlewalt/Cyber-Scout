"""The API contract web/notes.js relies on (notes on the team + match pages, "Wrong team?" identity fixes on the match
page): who can do what, the 423 / 429 answers the UI turns into messages, and the merge-then-PUT fix flow."""
import json

from .conftest import LAN, C  # noqa: F401

EV = "2026wasam"
M = f"{EV}_qm17"


def test_match_note_tagged_with_a_team_shows_on_both_pages(team):
    sc, v = team["scout"], team["viewer"]
    r = sc.post(f"/events/{EV}/notes", {"match": M, "team": "frc4911", "text": "4911 lost a wheel in shift 3\nback by endgame"})
    assert r.status_code == 201 and r.json()["author"]["display_name"]
    assert sc.post(f"/events/{EV}/notes", {"match": M, "text": "field fault at 1:10"}).status_code == 201
    assert sc.post(f"/events/{EV}/notes", {"team": "frc4911", "text": "fast intake from the depot"}).status_code == 201
    on_match = v.get(f"/events/{EV}/notes?match={M}").json()          # viewers read
    on_team = v.get(f"/events/{EV}/notes?team=frc4911").json()
    assert [n["text"] for n in on_match] == ["field fault at 1:10", "4911 lost a wheel in shift 3\nback by endgame"]
    assert {n["text"] for n in on_team} == {"fast intake from the depot", "4911 lost a wheel in shift 3\nback by endgame"}
    assert [n["match"] for n in on_team if n["match"]] == [M]               # the team page links it back to the match
    assert v.post(f"/events/{EV}/notes", {"team": "frc4911", "text": "viewer"}).status_code == 403   # viewers can't write
    assert v.get(f"/events/{EV}/notes?team=frc4911&include_removed=true").status_code == 403


def test_own_note_edit_delete_undo_and_strategist_hide_restore(team):
    sc, s = team["scout"], team["strategist"]
    nid = sc.post(f"/events/{EV}/notes", {"team": "frc254", "text": "first"}).json()["id"]
    r = sc.patch(f"/notes/{nid}", {"text": "first, edited"})
    assert r.status_code == 200 and r.json()["edited_by"] is None and r.json()["updated_at"] >= r.json()["created_at"]
    assert sc.delete(f"/notes/{nid}").status_code == 204                      # own -> deleted
    assert sc.get(f"/events/{EV}/notes?team=frc254").json() == []
    assert sc.post(f"/notes/{nid}/restore").status_code == 200                # the UI's "Undo"
    assert s.delete(f"/notes/{nid}").status_code == 204                       # strategist on someone else's -> hidden
    assert sc.post(f"/notes/{nid}/restore").status_code == 403               # author can't undo a hide
    rows = s.get(f"/events/{EV}/notes?team=frc254&include_removed=true").json()
    assert rows[0]["removed"] == "hidden" and rows[0]["removed_by"]["display_name"]
    assert s.post(f"/notes/{nid}/restore").status_code == 200
    assert sc.get(f"/events/{EV}/notes?team=frc254").json()[0]["text"] == "first, edited"


def test_lockdown_answers_423_for_notes_and_fixes(team):
    sc, s, admin = team["scout"], team["strategist"], team["admin"]
    nid = sc.post(f"/events/{EV}/notes", {"team": "frc254", "text": "before lockdown"}).json()["id"]
    assert admin.patch("/admin/security", {"lockdown": True}).status_code == 200
    assert sc.get("/session").json()["lockdown"] is True                    # the UI disables its forms from this
    assert sc.post(f"/events/{EV}/notes", {"team": "frc254", "text": "x"}).status_code == 423
    assert sc.patch(f"/notes/{nid}", {"text": "y"}).status_code == 423
    assert sc.put(f"/events/{EV}/overrides/{M}", {"red": {"0": "frc1"}}).status_code == 423
    assert s.delete(f"/notes/{nid}").status_code == 423
    assert sc.get(f"/events/{EV}/notes?team=frc254").status_code == 200      # reading still works
    assert admin.post(f"/events/{EV}/notes", {"team": "frc254", "text": "admin"}).status_code == 201
    assert admin.patch("/admin/security", {"lockdown": False}).status_code == 200


def test_fix_form_merges_with_applied_fixes_and_waits_for_review(team, data):
    sc, s = team["scout"], team["strategist"]
    p = data / "events" / EV / "overrides.json"
    r = s.put(f"/events/{EV}/overrides/{M}", {"red": {"0": "frc9442"}, "blue": {}})   # strategist: applied now
    assert r.status_code == 200 and r.json()["status"] == "applied"
    o = sc.get(f"/events/{EV}/overrides").json()
    cur = o["overrides"][M]
    entry = {"red": {**cur["red"], "1": "frc1318"}, "blue": {"3": "frc2903"}}         # what the form sends
    r = sc.put(f"/events/{EV}/overrides/{M}", entry)
    assert r.status_code == 202 and r.json()["status"] == "pending"                  # "Sent for review"
    pend = [x for x in sc.get(f"/events/{EV}/overrides").json()["pending"] if x["match"] == M]
    assert len(pend) == 1 and pend[0]["current"] == cur and not pend[0]["stale"] and pend[0]["by"]["display_name"]
    assert json.loads(p.read_text())[M] == cur                                         # nothing published yet
    assert s.post(f"/events/{EV}/override-requests/{pend[0]['id']}/approve").status_code == 200
    assert json.loads(p.read_text())[M] == entry
    # "Ask to clear" from a scout is queued; a strategist's "Clear fixes" applies (204)
    assert sc.delete(f"/events/{EV}/overrides/{M}").status_code == 202
    assert s.delete(f"/events/{EV}/overrides/{M}").status_code == 204
    assert M not in json.loads(p.read_text())
