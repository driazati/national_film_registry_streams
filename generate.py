from pickledict import jsondict
from justwatch import JustWatch
import wikipedia
from os import close
import textwrap
import sys
import time
import argparse
import logging
import pprint
import re
import pickle
from urllib.parse import urlparse


parser = argparse.ArgumentParser()
parser.add_argument('--source-tsv-file')
parser.add_argument('--country', default="US")
args = parser.parse_args()

jw = JustWatch(country=args.country)


remaps = {
    "The Battle of San Pietro": "San Pietro",
}

alldata = jsondict(save_on_every_write=False,
                   file_name="data.json")

FORMAT = "[%(asctime)s] %(levelname)s - %(message)s"
logging.basicConfig(format=FORMAT,
                    level=logging.INFO,
                    datefmt="%Y-%m-%d %H:%M:%S")


def clean(s: str):
    """
    Clean up string for comparing movie names
    """
    s = re.sub(r'\(.*\)', '', s)
    s = re.sub(
        r'\W+', '', s).lower().replace('é', 'e').replace('the', '')
    return s


def matches(name, release_year, item):
    """
    Check if a movie from a list of `name` with `release_year` matches a result
    from the justwatch API
    """
    name = remaps.get(name, name)
    item_title = clean(item['title'])
    name = clean(name)
    if not (item_title.startswith(name) or name.startswith(item_title)):
        return False

    item_release_year = item.get('original_release_year', float("inf"))
    if str(release_year) == str(item_release_year):
        return True

    if abs(int(release_year) - int(item_release_year)) < 4:
        return True

    return False


def get_domain(url):
    """
    Get the domain name from a full URL
    """
    loc = urlparse(url).netloc
    return '.'.join(loc.split(".")[-2:-1])


def get_movie(name, release_year):
    """
    Locate a movie in the justwatch API
    """
    key = f"{name} {release_year}"
    if key not in alldata:
        logging.info(f"Fetching {name}")
        alldata[key] = jw.search_for_item(name)
    results = alldata[key]

    if release_year is not None and release_year.isnumeric():
        for item in results['items']:
            if matches(name, release_year, item):
                return item

    return None


def find_streams(result):
    """
    Pull non-rent/buy options out of a justwatch API result
    """
    if 'offers' not in result:
        return None

    streams = []
    for stream in result['offers']:
        if stream.get("monetization_type", False) == "flatrate":
            url = stream['urls']['standard_web']
            type = stream['presentation_type'].upper()
            streams.append((get_domain(url), type, url))
        elif stream.get("monetization_type", False) == "ads":
            url = stream['urls']['standard_web']
            type = stream['presentation_type'].upper()
            streams.append((get_domain(url), type, url))
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
            seen[domain] = {
                type: url
            }

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


urls = jsondict(save_on_every_write=False,
                file_name="wiki.json")


def get_wiki_url(title, year):
    key = f"{title} {year}"
    if key in urls:
        return urls[key]

    wiki_url = None
    try:
        wiki_url = wikipedia.page(f"{title} {release_year}").url
    except wikipedia.exceptions.WikipediaException:
        try:
            wiki_url = wikipedia.page(f"{title} film {release_year}").url
        except wikipedia.exceptions.WikipediaException:
            pass

    urls[key] = wiki_url
    return wiki_url


def print_movie(name, release_year):
    """
    Look up a movie by name + release_year, find if it's available to stream anywhere,
    and then print it as a markdown table row
    """
    result = get_movie(name, release_year)

    if result is None:
        logging.info(f"Unable to find {name} {release_year}")
        print(f"| {name} | {release_year} | No data found |")
        return

    title = result['title']
    release_year = result['original_release_year']
    wiki_url = get_wiki_url(title, release_year)
    title_text = title
    if wiki_url is not None:
        title_text = f"[{title}]({wiki_url})"
    streams = find_streams(result)

    print(f"| {title_text} | {release_year} | {streams_to_text(streams)} |")


print(textwrap.dedent("""
    # Stream the National Film Registry

    This table shows streaming providers that show each of the movies from the Library of Congress' [National Film Registry](https://www.loc.gov/programs/national-film-preservation-board/film-registry/complete-national-film-registry-listing/).
""").strip())

print("\n")
print("| Name | Release Year | Stream URLs")
print("| ---- | ------------ | -----------")

if args.source_tsv_file is None or args.source_tsv_file == "-":
    f = sys.stdin
else:
    f = open(args.source_tsv_file, "r")

for line in f:
    release_year = None
    line = line.strip()
    line = line.split("\t")
    if len(line) > 1:
        name = line[0]
        release_year = line[1]
    else:
        name = line[0]
    if line != "":
        print_movie(name, release_year)
    line = f.readline()
