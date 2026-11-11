"""A Seerr that answers like the real one for whole sets (shapes taken from a real Seerr's answers): a film collection,
discover by studio, network and keyword with date and rating filters, 20 a page, list items carrying mediaInfo.status
but not their requests, title details with them, search for studios and keywords, and requests that change a title's
status. Made-up titles. `Seerr().use()` puts it behind seerr.py's HTTP calls; `calls` records what was asked."""
import re
from unittest import mock

import seerr


def film(n, title, date, rating, **more):
    return {"id": n, "title": title, "releaseDate": date, "voteAverage": rating, "posterPath": None, "backdropPath": None, "overview": "", **more}


class Seerr:
    def __init__(self):
        self.calls, self.status, self.requests, self.posted = [], {}, {}, []
        self.quota = None  # requests allowed before Seerr says the quota is used up
        self.down = False
        self.users = [{"id": 7, "username": "member", "displayName": "Member", "email": "m@example.test"}]
        # the Lantern Saga: five films, one not out yet
        self.collection = [film(1001 + i, f"Lantern {i + 1}", d, r) for i, (d, r) in enumerate(
            [("2012-05-01", 7.7), ("2015-05-01", 7.3), ("2018-04-27", 8.2), ("2019-04-26", 8.3), ("2999-12-18", 0)])]
        # Harbour Pictures (studio 7): 45 films, 1980 to 2024, ratings 5.0 to 9.4
        self.studio = [film(2001 + i, f"Harbour film {i + 1}", f"{1980 + i}-06-01", 5 + (i % 45) / 10) for i in range(45)]
        # Tin Channel (network 9): 30 shows
        self.network = [{"id": 3001 + i, "name": f"Tin show {i + 1}", "firstAirDate": f"{1995 + i}-01-10", "voteAverage": 6 + (i % 30) / 10} for i in range(30)]
        # the keyword "clockwork (universe)" (55): three films and two shows
        self.keyword_films = [film(4001 + i, f"Clockwork film {i + 1}", f"{2010 + i}-03-03", 7) for i in range(3)]
        self.keyword_shows = [{"id": 5001 + i, "name": f"Clockwork show {i + 1}", "firstAirDate": f"{2020 + i}-09-09", "voteAverage": 8} for i in range(2)]
        # a studio too big for one confirm: 300 films
        self.big = [film(6001 + i, f"Big film {i + 1}", "2000-01-01", 7) for i in range(300)]

    def use(self):
        """patch seerr.py's HTTP for as long as the returned patch is started"""
        return mock.patch.object(seerr.requests, "request", self)

    def _with_status(self, kind, item):
        st = self.status.get((kind, item["id"]))
        return {**item, "mediaInfo": {"status": st}} if st else dict(item)

    def _discover(self, kind, params):
        pool = {("movie", "studio", 7): self.studio, ("movie", "studio", 8): self.big, ("tv", "network", 9): self.network,
                ("movie", "keywords", 55): self.keyword_films, ("tv", "keywords", 55): self.keyword_shows}
        which = next((k for k in ("studio", "network", "keywords") if k in params), None)
        items = pool.get((kind, which, int(params[which])), []) if which else []
        date = "releaseDate" if kind == "movie" else "firstAirDate"
        pre = "primaryReleaseDate" if kind == "movie" else "firstAirDate"
        items = [i for i in items if (pre + "Gte" not in params or i[date] >= params[pre + "Gte"]) and (pre + "Lte" not in params or i[date] <= params[pre + "Lte"])
                 and i["voteAverage"] >= float(params.get("voteAverageGte", 0))]
        page = int(params.get("page", 1))
        return {"page": page, "totalPages": max(1, -(-len(items) // 20)), "totalResults": len(items),
                "results": [self._with_status(kind, i) for i in items[(page - 1) * 20:page * 20]]}

    def _title(self, kind, tid):
        every = {"movie": self.collection + self.studio + self.keyword_films + self.big, "tv": self.network + self.keyword_shows}[kind]
        item = next((i for i in every if i["id"] == tid), None)
        if item is None:
            return None
        info = {"status": self.status.get((kind, tid), 1), "requests": [{"status": s} for s in self.requests.get((kind, tid), [])]}
        more = {"seasons": [{"seasonNumber": 0}, {"seasonNumber": 1}, {"seasonNumber": 2}]} if kind == "tv" else {
            "collection": {"id": 100, "name": "The Lantern Saga"} if item in self.collection else None,
            "productionCompanies": [{"id": 7, "name": "Harbour Pictures"}], "keywords": [{"id": 55, "name": "clockwork (universe)"}]}
        return {**item, **more, "mediaInfo": info}

    def __call__(self, method, url, headers=None, params=None, json=None, timeout=None, **kw):
        path = url.split("/api/v1/", 1)[1]
        self.calls.append({"method": method, "path": path, "user": (headers or {}).get("X-Api-User"), "params": params, "json": json})
        if self.down:
            raise seerr.requests.ConnectionError("down")
        params = params or {}
        code, body = 200, {}
        if path.startswith("user"):
            body = {"results": self.users}
        elif path == "status":
            body = {"version": "stand-in"}
        elif path == "settings/main":
            body = {"applicationTitle": "Seerr"}
        elif path == "collection/100":
            body = {"id": 100, "name": "The Lantern Saga", "overview": "Five films.", "posterPath": None, "backdropPath": None,
                    "parts": [self._with_status("movie", p) for p in self.collection]}
        elif path in ("studio/7", "studio/8", "network/9", "keyword/55"):
            body = {"id": int(path.split("/")[1]), "name": {"studio/7": "Harbour Pictures", "studio/8": "Big Studio", "network/9": "Tin Channel",
                                                             "keyword/55": "clockwork (universe)"}[path], "logoPath": None}
        elif path in ("discover/movies", "discover/tv"):
            body = self._discover("movie" if path.endswith("movies") else "tv", params)
        elif path == "search/company":
            body = {"results": [{"id": 7, "name": "Harbour Pictures", "logo_path": None}]}
        elif path == "search/keyword":
            body = {"results": [{"id": 55, "name": "clockwork (universe)"}]}
        elif path == "media":
            body = {"results": [{"tmdbId": i, "mediaType": k} for (k, i), s in self.status.items() if s == 5]}
        elif m := re.fullmatch(r"(movie|tv)/(\d+)", path):
            body = self._title(m[1], int(m[2]))
            if body is None:
                code, body = 404, {"message": "Not found"}
        elif path == "request" and method == "POST":
            key = (json["mediaType"], json["mediaId"])
            if self.quota is not None and len(self.posted) >= self.quota:
                code, body = 403, {"message": "Movie Quota exceeded."}
            elif self.status.get(key, 1) in (2, 3, 4, 5):
                code, body = 409, {"message": "Request for this media already exists."}
            else:
                self.posted.append({**json, "user": (headers or {}).get("X-Api-User")})
                self.status[key] = 2
                self.requests.setdefault(key, []).append(1)
                code, body = 201, {"status": 1}
        elif path == "request":
            body = {"results": [], "pageInfo": {"pages": 0, "results": 0}}
        else:
            code, body = 404, {"message": "Not found"}
        return mock.Mock(ok=code < 400, status_code=code, content=b"x", text=str(body), json=lambda: body)
