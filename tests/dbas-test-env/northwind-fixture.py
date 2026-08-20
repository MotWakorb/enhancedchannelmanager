#!/usr/bin/env python3
"""Generate the "Northwind IPTV" documentation fixture served by nginx.

Everything produced here is FICTIONAL — invented provider, invented channel
names, invented listings, and credentials that read as fake on sight. Nothing
in this fixture is, or resembles, a real provider or a real credential. See
bead `enhancedchannelmanager-wfz8z`: screenshots taken against this environment
may end up committed, and byte scanners cannot see pixels.

Outputs, into the directory given as argv[1]:

    playlist.m3u    53 channels in 7 group-titles  -> ingested by Dispatcharr P,
                                                      which re-serves them as XC
    epg.xml         XMLTV for those 53, 3 days
    local.m3u        6 channels in 2 group-titles  -> a STANDARD M3U account on
                                                      instance A, so the M3U
                                                      Manager shows both account
                                                      types side by side
    local-epg.xml   XMLTV for those 6, 3 days
    logos/*.png     one 320x320 logo per channel

Deterministic: the RNG is seeded, so re-running reproduces the same playlists
and logos byte for byte. The XMLTV files are anchored to "today" in UTC, so
they move with the calendar — regenerate them when the listings go stale and
re-import the EPG sources.

Usage:
    python3 northwind-fixture.py /home/lecaptainc/ecm-docenv-fixture

Requires Pillow. Point NORTHWIND_FIXTURE_DIR at the output directory when
bringing up docker-compose.xc-provider.yml.
"""
import datetime as dt
import os
import random
import sys

from PIL import Image, ImageDraw, ImageFont

# --- the provider lineup, ingested by P and re-served over Xtream Codes -------
LINEUP = [
    ("Northwind News", [
        "Meridian News", "Meridian News HD", "Capitol Report", "Global Wire",
        "Beacon Business", "Continental 24", "Harbour Weather", "The Briefing",
    ]),
    ("Northwind Sports", [
        "Summit Sports 1", "Summit Sports 2", "Summit Sports HD", "Velodrome TV",
        "Gridiron Network", "Pitchside FC", "Court Vision", "Outdoor Pursuits",
        "Paddock Live", "Ringside",
    ]),
    ("Northwind Movies", [
        "Silverline Cinema", "Silverline Classics", "Silverline Action",
        "Nightscreen Thrillers", "Matinee Family", "Indie Reel",
        "Westward Westerns", "Orbit Sci-Fi", "Silverline 4K",
    ]),
    ("Northwind Kids", [
        "Sprout Junction", "Cartoon Cove", "Little Explorers",
        "Storybook TV", "Puzzle Pals", "Dino Dash",
    ]),
    ("Northwind Documentary", [
        "Terra Discovery", "Wild Frontier", "History Vault", "Deep Ocean",
        "Cosmos Files", "Engineering Marvels", "Culture Trail",
    ]),
    ("Northwind Entertainment", [
        "Primetime Plus", "Sitcom Central", "Reality Row", "Talk of the Town",
        "Stage & Screen", "Retro Rewind", "Lifestyle Loft", "Comedy Corner",
    ]),
    ("Northwind Music", [
        "Amp Rock", "Cadence Classical", "Groove Lounge", "Chart Pulse",
        "Bayou Country",
    ]),
]

# --- the small local lineup, a STANDARD M3U account on instance A ------------
LOCAL_LINEUP = [
    ("Northwind Local", ["Harbour City TV", "Lakeside Local", "Riverbend Community"]),
    ("Northwind Regional", ["Northern Counties", "Coastal Region One", "Valley Public"]),
]

PALETTE = [
    ("#1f3a5f", "#e8f0fa"), ("#7a2f2f", "#fbeaea"), ("#2f5f3a", "#e9f7ee"),
    ("#5a3f7a", "#f2eafb"), ("#7a5a1f", "#fbf3e2"), ("#1f5f5f", "#e6f6f6"),
    ("#4a4a52", "#eeeef2"),
]
LOCAL_PALETTE = [("#31465a", "#eaf1f7"), ("#4a5a2f", "#f0f5e6")]

PROGRAMMES = {
    "Northwind News": ["Morning Briefing", "World at One", "Market Watch",
                       "The Evening Report", "Newsline Tonight", "Weather Now",
                       "Correspondents' Round Table", "Headlines"],
    "Northwind Sports": ["Match of the Day", "League Round-Up", "Live: Regional Final",
                         "Transfer Desk", "Classic Encounters", "Training Ground",
                         "Sports Tonight", "Highlights Hour"],
    "Northwind Movies": ["Feature Presentation: The Long Road", "Late Night Double Bill",
                         "Classic Matinee: Harbour Lights", "Director's Cut",
                         "Feature: Northern Skies", "Feature: The Quiet Signal"],
    "Northwind Kids": ["Sprout Stories", "Cartoon Carnival", "Adventure Club",
                       "Sing-Along Hour", "Puzzle Time", "Bedtime Tales"],
    "Northwind Documentary": ["Life in the Canopy", "Ancient Engineering",
                              "Beneath the Waves", "Voyagers", "The Long Winter",
                              "Cities of Stone"],
    "Northwind Entertainment": ["Primetime Live", "Sitcom Block", "The Talk",
                                "Reality Check", "Retro Hour", "Late Show"],
    "Northwind Music": ["Rock Block", "Concert Hall", "Late Lounge",
                        "The Countdown", "Acoustic Sessions"],
    "Northwind Local": ["Local News at Six", "Council Report", "Community Notice Board",
                        "Town Hall", "Local Sport Round-Up", "Afternoon Movie"],
    "Northwind Regional": ["Regional Weather", "Around the Counties", "Farming Today",
                           "Regional Assembly", "Coast Report", "Late Local"],
}

DEJAVU_BOLD = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
DEJAVU = "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"


def slugify(name):
    return "".join(
        c.lower() if c.isalnum() else "-" for c in name
    ).strip("-").replace("--", "-")


def _font(path, size):
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def make_logo(path, text, fg, bg, footer):
    img = Image.new("RGBA", (320, 320), bg)
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([12, 12, 307, 307], radius=36, outline=fg, width=8)
    d.rectangle([12, 210, 307, 260], fill=fg)
    initials = "".join(w[0] for w in text.split() if w[0].isalnum())[:3].upper()
    d.text((160, 130), initials, font=_font(DEJAVU_BOLD, 108), fill=fg, anchor="mm")
    d.text((160, 235), footer, font=_font(DEJAVU, 26 if len(footer) > 5 else 24),
           fill=bg, anchor="mm")
    img.save(path, "PNG")


def build(out, lineup, palette, footer, m3u_name, xml_name, generator, days=3):
    """Write one playlist + its XMLTV + its logos. Returns (channels, programmes)."""
    base_day = dt.datetime.now(dt.timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0)
    m3u = ["#EXTM3U"]
    xml = ['<?xml version="1.0" encoding="UTF-8"?>',
           f'<tv generator-info-name="{generator}">']
    programmes = []
    count = 0

    for gi, (group, channels) in enumerate(lineup):
        fg, bg = palette[gi % len(palette)]
        for name in channels:
            count += 1
            slug = slugify(name)
            tvg_id = f"{slug}.northwind.example"
            logo_rel = f"logos/{slug}.png"
            make_logo(os.path.join(out, logo_rel), name, fg, bg, footer)
            logo_url = f"http://provider-northwind/{logo_rel}"

            m3u.append(
                f'#EXTINF:-1 tvg-id="{tvg_id}" tvg-name="{name}" '
                f'tvg-logo="{logo_url}" group-title="{group}",{name}')
            m3u.append(f"http://provider-northwind/stream/{slug}.ts")
            xml.append(f'  <channel id="{tvg_id}">'
                       f'<display-name>{name}</display-name>'
                       f'<icon src="{logo_url}" /></channel>')

            titles = PROGRAMMES[group]
            for day in range(days):
                cursor = base_day + dt.timedelta(days=day)
                end_of_day = cursor + dt.timedelta(days=1)
                idx = 0
                while cursor < end_of_day:
                    mins = random.choice([30, 30, 60, 60, 60, 90, 120])
                    stop = min(cursor + dt.timedelta(minutes=mins), end_of_day)
                    title = titles[idx % len(titles)]
                    idx += 1
                    programmes.append(
                        f'  <programme start="{cursor.strftime("%Y%m%d%H%M%S")} +0000" '
                        f'stop="{stop.strftime("%Y%m%d%H%M%S")} +0000" '
                        f'channel="{tvg_id}">'
                        f'<title lang="en">{title}</title>'
                        f'<desc lang="en">{title} on {name}. '
                        f'Fictional listing for documentation.</desc>'
                        f'<category lang="en">{group.replace("Northwind ", "")}</category>'
                        f'</programme>')
                    cursor = stop

    xml.extend(programmes)
    xml.append("</tv>")
    with open(os.path.join(out, m3u_name), "w") as fh:
        fh.write("\n".join(m3u) + "\n")
    with open(os.path.join(out, xml_name), "w") as fh:
        fh.write("\n".join(xml) + "\n")
    return count, len(programmes)


def main():
    if len(sys.argv) < 2:
        sys.exit(f"usage: {sys.argv[0]} <output-dir>")
    out = sys.argv[1]
    os.makedirs(os.path.join(out, "logos"), exist_ok=True)

    random.seed(20260820)
    n1, p1 = build(out, LINEUP, PALETTE, "NORTHWIND",
                   "playlist.m3u", "epg.xml", "northwind-fixture")
    random.seed(9021)
    n2, p2 = build(out, LOCAL_LINEUP, LOCAL_PALETTE, "LOCAL",
                   "local.m3u", "local-epg.xml", "northwind-local-fixture")

    print(f"playlist.m3u : {n1} channels in {len(LINEUP)} groups, "
          f"epg.xml {p1} programmes")
    print(f"local.m3u    : {n2} channels in {len(LOCAL_LINEUP)} groups, "
          f"local-epg.xml {p2} programmes")
    print(f"logos/       : {n1 + n2} PNGs")
    print(f"written to   : {out}")


if __name__ == "__main__":
    main()
