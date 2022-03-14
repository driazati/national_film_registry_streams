import concurrent.futures
from pickledict import jsondict
from justwatch import JustWatch
import wikipedia
import json
import textwrap
import requests
import argparse
import logging
import re
import os
import concurrent
from pathlib import Path
from urllib.parse import urlparse
from bs4 import BeautifulSoup
from typing import *

parser = argparse.ArgumentParser()
parser.add_argument("--country", default="US")
args = parser.parse_args()

jw = JustWatch(country=args.country)
DEBUG = os.getenv("DEBUG", "") == "1"


remaps = {
    "The Battle of San Pietro": "San Pietro",
}

alldata = jsondict(save_on_every_write=False, file_name="data.json")
urls = jsondict(save_on_every_write=False, file_name="wiki.json")

FORMAT = "[%(asctime)s] %(levelname)s - %(message)s"
logging.basicConfig(format=FORMAT, level=logging.INFO, datefmt="%Y-%m-%d %H:%M:%S")


def clean(s: str):
    """
    Clean up string for comparing movie names
    """
    s = re.sub(r"\(.*\)", "", s)
    s = (
        re.sub(r"\W+", "", s)
        .lower()
        .replace("é", "e")
        .replace("the", "")
        .replace("and", "")
        .replace("&", "")
    )
    return s


def matches(name, release_year, item):
    """
    Check if a movie from a list of `name` with `release_year` matches a result
    from the justwatch API
    """
    name = remaps.get(name, name)
    item_title = clean(item["title"])
    name = clean(name)
    if not (item_title.startswith(name) or name.startswith(item_title)):
        return False

    item_release_year = item.get("original_release_year", float("inf"))
    if str(release_year) == str(item_release_year):
        return True

    if abs(int(release_year) - int(item_release_year)) < 4:
        return True

    return False


def jw_lookup(name):
    cache_dir = Path(".jw")
    cache_dir.mkdir(exist_ok=True)
    cache_path = cache_dir / clean(name)
    if cache_path.exists():
        with open(cache_path) as f:
            return json.load(f)
    else:
        r = jw.search_for_item(name)
        with open(cache_path, "w") as f:
            json.dump(r, f)
        return r


def get_domain(url):
    """
    Get the domain name from a full URL
    """
    loc = urlparse(url).netloc
    return ".".join(loc.split(".")[-2:-1])


def get_movie(name, release_year):
    """
    Locate a movie in the justwatch API
    """
    try:
        # key = f"{name} {release_year}"
        # if key not in alldata:
        #     logging.info(f"Fetching {name}")
        #     alldata[key] = jw.search_for_item(name)
        # else:
        #     logging.info(f"Using cached {name}")
        results = jw_lookup(name)

        # with open("r.json", "w") as f:
        #     f.write(json.dumps(results, indent=2))
        if release_year is not None and release_year.isnumeric():
            for item in results["items"]:
                if matches(name, release_year, item):
                    return item
    except Exception as e:
        raise e
        pass

    return None


def find_streams(result):
    """
    Pull non-rent/buy options out of a justwatch API result
    """
    if "offers" not in result:
        return None

    PLATFORM_NAMES = {
        "vdu": "Vudu",
        "amp": "Amazon Prime Video",
        "drv": "DirecTV",
        "hop": "Hoopla",
        "knp": "Kanopy",
        "hbm": "HBO Max",
        "crc": "Criterion",
        "dnp": "Disney Plus",
        "nfx": "Netflix",
    }

    streams = []
    for stream in result["offers"]:
        if stream.get("monetization_type", False) in {"flatrate", "ads", "free"}:
            url = stream["urls"]["standard_web"]
            type = stream["presentation_type"].upper()
            if stream["package_short_name"] in {"afa"}:
                continue

            if stream["package_short_name"] in PLATFORM_NAMES:
                stream_platform_name = PLATFORM_NAMES[stream["package_short_name"]]
            else:
                logging.info(
                    f"Unknown package short name: {stream['package_short_name']}: {url}"
                )
                stream_platform_name = get_domain(url)
            streams.append((stream_platform_name, type, url))
    return streams


def streams_to_text(streams) -> str:
    """
    Convert a list of streams from `find_streams` to printable text
    """
    if streams is None:
        return "Not available anywhere"

    if len(streams) == 0:
        return "Not available to stream"

    seen = {}
    streams_to_print = []
    for domain, type, url in streams:
        if domain in seen:
            seen[domain][type] = url
        else:
            seen[domain] = {type: url}

    for domain, items in seen.items():
        if len(items) == 1:
            url = list(items.values())[0]
            streams_to_print.append(f"[{domain}]({url})")
        elif len(items) > 1 and "HD" in items:
            # only print the HD stream
            url = items["HD"]
            streams_to_print.append(f"[{domain}]({url})")
        else:
            for type, url in items.items():
                streams_to_print.append(f"[{domain} {type}]({url})")
    return " <br/> ".join(streams_to_print)


def get_wiki_url(title, year):
    key = f"{title} {year}"
    if key in urls:
        return urls[key]

    wiki_url = "https://www.loc.gov/programs/national-film-preservation-board/film-registry/descriptions-and-essays/"
    try:
        wiki_url = wikipedia.page(f"{title} {year}").url
    except wikipedia.exceptions.WikipediaException:
        try:
            wiki_url = wikipedia.page(f"{title} film {year}").url
        except wikipedia.exceptions.WikipediaException:
            pass

    urls[key] = wiki_url
    return wiki_url


def get_movie_row(args):
    """
    Look up a movie by name + release_year, find if it's available to stream anywhere,
    and then print it as a markdown table row
    """
    name, release_year, year_added = args
    result = get_movie(name, release_year)

    if result is None:
        logging.info(f"No result for {name} {release_year}")
        return [name, None, release_year, year_added, "No data found"]

    title = result["title"]
    release_year = result["original_release_year"]
    url = get_wiki_url(title, release_year)

    try:
        streams = find_streams(result)
    except Exception as e:
        logging.info(f"Unable to find {name} {release_year}: {e}")
        return [title, url, release_year, year_added, "No data found"]

    return [title, url, release_year, year_added, streams_to_text(streams)]


def get_registry() -> List[Tuple[str, str, str]]:
    """
    Fetch the Library of Congress film registry
    """
    url = "https://www.loc.gov/programs/national-film-preservation-board/film-registry/complete-national-film-registry-listing/"
    if DEBUG:
        logging.info("Using cached registry")
        with open("out.html") as f:
            html = f.read()
    else:
        logging.info("Fetching registry")
        html = requests.get(url).content.decode()

    # Fix some random badness in the HTML
    html = html.replace("					</td>", "					</th>")
    soup = BeautifulSoup(html, features="html.parser")

    registry = []
    body = soup.select("table.sortable-table > tbody")[0]
    for tr in body.find_all("tr"):
        title = tr.contents[1].text.strip()
        year_released = tr.contents[3].text.strip()
        year_added = tr.contents[5].text.strip()
        registry.append((title, year_released, year_added))

    return registry


if __name__ == "__main__":
    args = get_registry()
    # args = args[-5:]

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        processes = list(pool.map(get_movie_row, args))

    results = list(sorted(processes, key=lambda x: x[0].lstrip("The ").lower()))

    to_print = []
    for name, url, release_year, year_added, text in results:
        if url is None:
            linked = name
        else:
            linked = f"[{name}]({url})"
        to_print.append(
            "| " + " | ".join([linked, str(release_year), str(year_added), text]) + " |"
        )

    print(
        textwrap.dedent(
            """
        # Stream the National Film Registry

        This table shows streaming providers that show each of the movies from the Library of Congress' [National Film Registry](https://www.loc.gov/programs/national-film-preservation-board/film-registry/complete-national-film-registry-listing/).
    """
        ).strip()
    )

    print("\n")
    print("| Name | Release Year | Year Added | Stream URLs |")
    print("| ---- | ------------ | ---------- | ----------- |")

    print("\n".join(to_print))
