"""Questarr's game lookups answered from Steam's store (app/gamelookup.py): IGDB's paths, query language and field
names over a stand-in Steam, and nobody but the holder of the key Parlor wrote for Questarr gets an answer."""
import json
import unittest
from unittest import mock

from yeerr_test_env import TMP, client

import gamelookup
import stack

DAY = 86400
NOW = 1_800_000_000


def item(appid, name, tags=(), when=NOW - 400 * DAY, kind=0, **more):
    return {"appid": appid, "id": appid, "success": 1, "type": kind, "name": name, "tags": [{"tagid": t, "weight": 9} for t in tags],
            "basic_info": {"short_description": f"About {name}.", "developers": [{"name": "Makers"}], "publishers": [{"name": "Sellers"}]},
            "assets": {"asset_url_format": f"steam/apps/{appid}/${{FILENAME}}?t=1", "library_capsule": "abc/library_capsule.jpg", "header": "abc/header.jpg"},
            "release": {"steam_release_date": when}, "platforms": {"windows": True, "mac": False, "steamos_linux": True},
            "reviews": {"summary_filtered": {"percent_positive": 97, "review_count": 1234}},
            "screenshots": {"all_ages_screenshots": [{"filename": f"steam/apps/{appid}/ss_1.jpg"}]}, **more}


RPG, HORROR, HENTAI, SINGLE = 122, 1667, 9130, 4182
SHOP = [item(10, "Doom", [19, SINGLE]), item(20, "Doom Eternal", [19, HORROR]), item(30, "Old Scrolls", [RPG, 1684], when=NOW - 9000 * DAY),
        item(40, "Fresh Quest", [RPG], when=NOW - 5 * DAY), item(50, "Soon Quest", [RPG], when=NOW + 20 * DAY, release={"steam_release_date": NOW + 20 * DAY}),
        item(60, "Console of Steam", kind=10), item(70, "Doom: The Add-on", [19], kind=4),
        item(80, "After Dark", [RPG, HENTAI], content_descriptorids=[3]), {"appid": 99, "success": 15, "visible": False}]


class Steam:
    """Steam's public store, as far as the lookup asks it."""

    def __init__(self):
        self.calls, self.fail = [], None

    def __call__(self, url, **params):
        self.calls.append((url, params))
        if self.fail:
            raise gamelookup.HTTPException(self.fail, "Steam's store didn't answer")
        if url.endswith("/storesearch/"):
            words = params["term"].lower().split()
            return {"items": [{"type": "app", "id": i["appid"], "name": i["name"]} for i in SHOP if "name" in i and all(w in i["name"].lower() for w in words)]}
        body = json.loads(params["input_json"])
        assert body["context"] == gamelookup.CONTEXT and body["data_request"]["include_assets"]
        if "GetItems" in url:
            return {"response": {"store_items": [i for i in SHOP if i["appid"] in [x["appid"] for x in body["ids"]]]}}
        q = body["query"]
        f = q["filters"]
        tags = lambda i: {t["tagid"] for t in i.get("tags", [])}  # noqa: E731
        hits = [i for i in SHOP if i.get("type") == 0
                and (i["release"]["steam_release_date"] > NOW) == bool(f.get("coming_soon_only"))
                and all(tags(i) & {int(t) for t in g["tagids"]} for g in f["tagids_must_match"])
                and not tags(i) & {int(t) for t in f["tagids_exclude"]}]
        return {"response": {"store_items": hits[q["start"]:q["start"] + q["count"]]}}


class Lookup(unittest.TestCase):
    def setUp(self):
        self.steam = Steam()
        self.key = TMP / "lookup.key"
        self.key.write_text("s3cret-key\n")
        for target, value in ((("steam",), self.steam), (("KEY",), self.key), (("_now",), lambda: NOW)):
            p = mock.patch.object(gamelookup, *target, value)
            p.start()
            self.addCleanup(p.stop)
        self.c = client()

    def ask(self, endpoint, query, key="s3cret-key", status=200):
        r = self.c.post(f"/lookup/games/v4/{endpoint}", content=query, headers={"x-parlor-lookup": key, "client-id": "parlor"})
        self.assertEqual(r.status_code, status, r.text)
        return r.json()

    def names(self, query):
        return [g["name"] for g in self.ask("games", query)]

    def test_a_title_search_answers_in_igdbs_shape(self):
        got = self.ask("games", 'search "doom"; fields name, cover.url; limit 40;')
        self.assertEqual([g["name"] for g in got], ["Doom", "Doom Eternal", "Doom: The Add-on"], "found by title, and what isn't a game is left out")
        g = got[0]
        self.assertEqual((g["id"], g["summary"], g["first_release_date"], g["rating"], g["aggregated_rating_count"]), (10, "About Doom.", NOW - 400 * DAY, 97, 1234))
        self.assertEqual(g["cover"]["url"], "//shared.akamai.steamstatic.com/store_item_assets/steam/apps/10/abc/library_capsule.jpg?t=1")
        self.assertEqual(g["screenshots"], [{"url": "//shared.akamai.steamstatic.com/store_item_assets/steam/apps/10/ss_1.jpg"}])
        self.assertEqual(g["platforms"], [{"id": 6, "name": "PC (Microsoft Windows)"}, {"id": 3, "name": "Linux"}])
        self.assertEqual((g["genres"], g["themes"], g["game_modes"]), ([], [{"id": 1, "name": "Action"}], [{"id": 1, "name": "Single player"}]))
        self.assertEqual(g["involved_companies"], [{"company": {"name": "Makers"}, "developer": True, "publisher": False},
                                                   {"company": {"name": "Sellers"}, "developer": False, "publisher": True}])
        self.assertEqual(g["websites"], [{"category": 13, "url": "https://store.steampowered.com/app/10"}])
        self.assertEqual((g["game_type"], got[2]["game_type"]), (0, 1), "an add-on is one in IGDB's numbers too")
        self.assertNotIn("age_ratings", g)

    def test_every_way_questarr_words_a_title(self):
        for query in ('search "doom eternal"; fields name; where category = 0; limit 40;', 'fields name; where name ~= "Doom Eternal"; limit 40;',
                      'fields name; where name ~ *"doom eternal"*; sort rating desc; limit 40;', 'fields name; where name ~ *"doom"* & name ~ *"eternal"*; limit 1;'):
            self.assertEqual(self.names(query), ["Doom Eternal"], query)
        self.assertEqual(self.names('search "nothing like it"; fields name; limit 40;'), [])
        # what's in quotes is a title, whatever it reads like
        for title in ("id = 10", "version_parent 3", "first_release_date >= 99999999999", "genres = (12)"):
            self.assertEqual(self.names(f'fields name; where name ~ *"{title}"*; limit 5;'), [], title)
            self.assertEqual(self.steam.calls[-1][1]["term"], title)

    def test_games_by_id_keep_the_order_asked_and_skip_what_isnt_there(self):
        self.assertEqual(self.names("fields name; where id = 30;"), ["Old Scrolls"])
        self.assertEqual(self.names("fields name; where id = (40,99,60,10); limit 100;"), ["Fresh Quest", "Doom"])
        self.assertEqual(self.ask("games", "fields name; where version_parent = 10; sort first_release_date asc; limit 30;"), [], "no editions known")

    def test_lists_without_a_title_come_from_steams_popular_order(self):
        popular = self.names("fields name; where rating > 80 & rating_count > 10; sort rating desc; limit 20;")
        self.assertEqual(popular, ["Doom", "Doom Eternal", "Old Scrolls", "Fresh Quest", "After Dark"], "released games only")
        self.assertEqual(self.names(f"fields name; where first_release_date >= {NOW - 30 * DAY} & first_release_date <= {NOW}; sort first_release_date desc; limit 20;"),
                         ["Fresh Quest"], "new this month")
        self.assertEqual(self.names(f"fields name; where first_release_date >= {NOW} & first_release_date <= {NOW + 180 * DAY}; sort first_release_date asc; limit 20;"),
                         ["Soon Quest"], "out soon")
        self.assertEqual(self.names("fields name; where rating > 80 & rating_count > 10; sort rating desc; limit 2; offset 2;"), ["Old Scrolls", "Fresh Quest"])

    def test_genres_themes_and_what_to_leave_out(self):
        self.assertEqual(self.names('fields name; where genres.name ~ *"Role-playing"* & rating > 60 & rating_count > 3; sort rating desc; limit 20; offset 0;'),
                         ["Old Scrolls", "Fresh Quest", "After Dark"])
        self.assertEqual(self.names('fields name; where (genres.name ~ *"Role-playing (RPG)"* | genres.name ~ *"Puzzle"*) & rating > 80 & rating_count > 10 & id != (30,80); sort rating desc; limit 20;'),
                         ["Fresh Quest"])
        discover = "fields name, themes.id; where game_type = (0,8,9,10,11) & cover != null & {}; sort aggregated_rating_count desc; limit 20; offset 0;"
        self.assertEqual(self.names(discover.format("themes = (1) & themes = !(19)")), ["Doom"], "action, never horror")
        self.assertEqual(self.names(discover.format("genres = (12) & themes = !(42)")), ["Old Scrolls", "Fresh Quest"], "no erotic games when they're filtered out")
        self.assertEqual(self.names(discover.format(f"genres = (12) & first_release_date >= {NOW - 30 * DAY}")), ["Fresh Quest"])
        self.assertEqual(self.names('search "quest"; ' + discover.format("genres = (12)")), ["Fresh Quest", "Soon Quest"], "a title and a genre together")
        self.assertEqual(self.names(discover.format("genres = (99)")), [], "a genre Steam has no tag for")
        self.assertEqual(self.names('fields name; where (platforms.name ~ *"PlayStation"*) & rating > 80 & rating_count > 10; sort rating desc; limit 20;'), [])
        self.assertEqual(len(self.names('fields name; where (platforms.name ~ *"PC"*) & rating > 80 & rating_count > 10; sort rating desc; limit 20;')), 5)

    def test_adult_games_carry_the_marks_questarrs_filters_read(self):
        g = self.ask("games", "fields name; where id = 80;")[0]
        self.assertIn({"id": 42, "name": "Erotic"}, g["themes"])
        self.assertEqual(g["age_ratings"], [{"category": 2, "rating": 5}])

    def test_the_other_things_questarr_asks(self):
        many = self.ask("multiquery", 'query games "q0" { fields name, cover.url; where name ~ *"doom"* & name ~ *"eternal"*; limit 1; };\n'
                                      'query games "q1" { fields name; where name ~ *"nowhere"*; limit 1; };\n'
                                      'query games "q2" { fields name; where name ~ *"}"* & name ~ *"doom"* & name ~ *"eternal"*; limit 1; };')
        self.assertEqual([(m["name"], [g["name"] for g in m["result"]]) for m in many], [("q0", ["Doom Eternal"]), ("q1", []), ("q2", [])], "a brace in a title ends nothing")
        self.assertEqual(self.steam.calls[-1][1]["term"], "} doom eternal")
        genres = self.ask("genres", "fields id, name; sort name asc; limit 50;")
        self.assertEqual(len(genres), 23)
        self.assertEqual(genres[0], {"id": 31, "name": "Adventure"})
        self.assertEqual(self.ask("platforms", "fields id, name; sort name asc; limit 100; offset 0;")[-1], {"id": 6, "name": "PC (Microsoft Windows)"})
        self.assertEqual(self.ask("platforms", "fields id, name; sort name asc; limit 100; offset 100;"), [])
        self.assertEqual(self.ask("external_games", 'fields game; where uid = "367520" & external_game_source = 1; limit 1;'), [{"game": 367520, "uid": "367520"}])
        self.assertEqual(self.ask("external_games", 'fields game, uid; where uid = ("10","20") & external_game_source = 1; limit 500;'),
                         [{"game": 10, "uid": "10"}, {"game": 20, "uid": "20"}])
        self.assertEqual(self.ask("game_time_to_beats", "fields game_id, normally; where game_id = (10,20);"), [])
        self.assertEqual(len(self.steam.calls), 4, "only the titles were asked of Steam: three searches, one set of store entries")

    def test_only_the_holder_of_the_key_gets_an_answer(self):
        for key in ("", "wrong", "s3cret-ke"):
            self.assertEqual(self.ask("games", 'search "doom";', key=key, status=401)["detail"], "Only Parlor's own Questarr may ask")
            self.assertEqual(self.c.post("/lookup/games/oauth2/token", headers={"x-parlor-lookup": key}).status_code, 401)
        self.assertEqual(self.c.post("/lookup/games/v4/games", content='search "doom";').status_code, 401)
        self.key.write_text("")  # no key written yet: nobody, not even an empty one
        self.ask("games", 'search "doom";', key="", status=401)
        self.key.write_bytes(b"\xff\xfe not text")
        self.ask("games", 'search "doom";', key="", status=401)
        self.key.unlink()
        self.ask("games", 'search "doom";', key="", status=401)
        self.assertEqual(self.steam.calls, [])

    def test_the_token_twitch_would_give(self):
        r = self.c.post("/lookup/games/oauth2/token", content="client_id=parlor&client_secret=parlor&grant_type=client_credentials", headers={"x-parlor-lookup": "s3cret-key"})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["access_token"])
        self.assertTrue(120 < r.json()["expires_in"] <= 3600, "Questarr asks again a minute early; short, so real IGDB keys given later take over soon")

    def test_steam_away_or_busy_is_said_in_a_way_questarr_understands(self):
        self.steam.fail = 502
        self.ask("games", 'search "doom";', status=502)
        self.steam.fail = 429  # Questarr waits and asks again on this one
        self.ask("games", "fields name; where id = 10;", status=429)
        self.ask("games", "x" * 20_000, status=413)

    def test_a_question_in_no_shape_at_all_is_answered_with_nothing(self):
        for query in ("", ";;;", "where", "limit many; offset -3;", "limit ²; offset ٣;", "fields name; where id = ();", 'query games "q" {', 'query games "q" { limit ²; };'):
            for endpoint in ("games", "multiquery", "genres", "nonsense"):
                self.ask(endpoint, query)


class Wire(unittest.TestCase):
    def setUp(self):
        gamelookup._kept.clear()
        self.addCleanup(gamelookup._kept.clear)

    def test_steams_answers_are_kept_a_quarter_of_an_hour(self):
        now = [NOW]
        made = mock.Mock(status_code=200, json=lambda: {"items": []})
        with mock.patch.object(gamelookup.requests, "get", return_value=made) as get, mock.patch.object(gamelookup, "_now", lambda: now[0]):
            for _ in range(3):
                self.assertEqual(gamelookup.steam("https://store.steampowered.com/api/storesearch/", term="doom", cc="us"), {"items": []})
            self.assertEqual(get.call_count, 1, "asked once")
            gamelookup.steam("https://store.steampowered.com/api/storesearch/", term="quake", cc="us")
            self.assertEqual(get.call_count, 2, "another title is another question")
            now[0] += gamelookup.KEPT + 1
            gamelookup.steam("https://store.steampowered.com/api/storesearch/", term="doom", cc="us")
            self.assertEqual(get.call_count, 3, "and asked again once it's old")
            made.status_code = 429
            with self.assertRaises(gamelookup.HTTPException):
                gamelookup.steam("https://store.steampowered.com/api/storesearch/", term="new", cc="us")
            made.status_code = 200
            gamelookup.steam("https://store.steampowered.com/api/storesearch/", term="new", cc="us")
            self.assertEqual(get.call_count, 5, "a refusal isn't kept")

    def test_steams_answers_are_turned_into_errors_not_crashes(self):
        class R:
            status_code = 200

            def json(self):
                raise ValueError("not JSON")
        for made, status in ((R(), 502), (mock.Mock(status_code=429), 429), (mock.Mock(status_code=403), 502)):
            with mock.patch.object(gamelookup.requests, "get", return_value=made) as get, self.assertRaises(gamelookup.HTTPException) as e:
                gamelookup.steam("https://store.steampowered.com/api/storesearch/", term="doom")
            self.assertEqual(e.exception.status_code, status)
            self.assertEqual(get.call_args.kwargs["allow_redirects"], False)
        with mock.patch.object(gamelookup.requests, "get", side_effect=gamelookup.requests.ConnectionError("down")), self.assertRaises(gamelookup.HTTPException) as e:
            gamelookup.steam("https://store.steampowered.com/api/storesearch/", term="doom")
        self.assertEqual(e.exception.status_code, 502)

    def test_ids_are_asked_fifty_at_a_time(self):
        asked = []
        with mock.patch.object(gamelookup, "steam", lambda url, **p: asked.append(len(json.loads(p["input_json"])["ids"])) or {"response": {}}):
            self.assertEqual(gamelookup.items(list(range(1, 121))), [])
        self.assertEqual(asked, [50, 50, 20])


class Key(unittest.TestCase):
    def test_setup_writes_the_key_once_where_both_can_read_it(self):
        root = TMP / "compose-lookup"
        root.mkdir()
        self.assertIn("questarr: a key for its game lookups", stack.init_files(root))
        key = root / "questarr" / "config" / "parlor-lookup.key"
        first = key.read_text()
        self.assertGreaterEqual(len(first.strip()), 40)
        self.assertEqual(key.stat().st_mode & 0o777, 0o600)
        self.assertNotIn("questarr: a key for its game lookups", stack.init_files(root))
        self.assertEqual(key.read_text(), first, "the same key on every start")
        key.write_text("")  # cut short (a full disk): made again, or nobody could ever ask
        self.assertIn("questarr: a key for its game lookups", stack.init_files(root))
        self.assertGreaterEqual(len(key.read_text().strip()), 40)
        sent = key.with_name("parlor-lookup.cjs")  # the file the compose loads into Questarr: always this version's
        self.assertEqual(sent.read_text(), stack.QUESTARR_LOOKUP.read_text())
        sent.write_text("// an older one")
        self.assertIn("questarr: game lookups go to Parlor", stack.init_files(root))
        self.assertEqual(sent.read_text(), stack.QUESTARR_LOOKUP.read_text())


if __name__ == "__main__":
    unittest.main()
