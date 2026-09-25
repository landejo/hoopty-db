"""Replay real page text captured from the browser pane (2026-09-24, signed-in
Facebook; CarGurus/Autotrader as a normal browser sees them) through the running
sandbox server's availability endpoints, and score against ground truth read by eye.
Sites block automated browsers, so this is the closest to the user's own Chrome."""
import json
import urllib.request

BASE = "http://127.0.0.1:8766"
FB_NAV = ("Marketplace\nEdit Marketplace Settings\nBrowse all\nNotifications\nInbox\nMarketplace access\nBuying\nSelling\n"
          "Create new listing\nCreate multiple listings\nLocation\nHollister, California\n · Within 40 mi\nCategories\nVehicles\n"
          "Property Rentals\nApparel\nClassifieds\nElectronics\nEntertainment\nFamily\nFree Stuff\nGarden & Outdoor\nHobbies\n"
          "Home Goods\nHome Improvement Supplies\nHome Sales\nMusical Instruments\nOffice Supplies\nPet Supplies\nSporting Goods\n"
          "Toys & Games\nBuy and sell groups\n")
CG_GOT_AWAY = ("All results\nLooks like that one got away\nSimilar cars to consider\n2023 MINI Cooper\n14,925 miles\nGood Deal\n$25,071\n"
               "Price includes fees\nGlendale, CA\n2023 MINI Cooper S 4-Door Hatchback FWD\n58,568 miles\nFair Deal\n$20,619\n"
               "No additional dealer fees\nEl Monte, CA\n2020 MINI Cooper S 2-Door Hatchback FWD\n33,721 miles\nGood Deal\n$19,121\n")

CASES = [  # (listing id, site, page_url, text, truth)
    (67, "facebook", "https://www.facebook.com/marketplace/item/1340534464945851/",
     FB_NAV + "0:00 / 1:00\nSold\n · 2000 BMW z3 Coupe 2D\n$16,000\nListed 15 weeks ago in Navarre, FL\nSave\nShare\nAbout this vehicle\n"
     "Driven 203,000 miles\nManual transmission\nExterior color: Silver · Interior color: Black\nClean title", "sold"),
    (52, "facebook", "https://www.facebook.com/marketplace/103985452972469/?unavailable_product=1",
     "This listing isn't available anymore\nIt may have been sold or expired. Take a look at these other items below.\nToday's picks\n"
     "Hollister\n · 40 mi\n$22,000\n2004 Lexus LX 470 Sport Utility 4D\n$24,999\n2006 Porsche cayman s manual\nLivermore, CA\n", "sold"),
    (83, "facebook", "https://www.facebook.com/marketplace/item/3043954789280914/",
     FB_NAV + "2008 Lexus gx470\n$15,000\nListed 3 weeks ago in San Jose, CA\nMessage\nAbout this vehicle\n176,000 miles\nAutomatic transmission\n"
     "Clean title", "active"),
    (111, "facebook", "https://www.facebook.com/marketplace/item/1662398978324248/",
     FB_NAV + "2010 Lexus GX 460 Premium Sport Utility 4D\n$22,500\nListed 9 weeks ago in Redwood City, CA\nMessage\nAbout this vehicle\n"
     "Driven 128,000 miles", "active"),
    (41, "facebook", "https://www.facebook.com/marketplace/item/835576954908739/",
     FB_NAV + "0:00 / 0:34\n1999 BMW z3 M Coupe 2D\n$55,000\nListed 2 years ago in Austin, TX\nMessage\nAbout this vehicle\n"
     "Driven 18,000 miles\nManual transmission", "active"),
    (59, "facebook", "https://www.facebook.com/marketplace/item/2558813731166472/",
     FB_NAV + "1999 BMW m Roadster 2D\n$8,000\nListed 48 weeks ago in Talent, OR\nMessage\nAbout this vehicle\n", "active"),
    (62, "facebook", "https://www.facebook.com/marketplace/item/1228157668861358/",
     FB_NAV + "2001 BMW z3\n$15,250\nListed a year ago in Columbia, SC\nMessage\nAbout this vehicle\n", "active"),
    (4, "cargurus", "https://www.cargurus.com/details/457181379", CG_GOT_AWAY, "sold"),
    (8, "cargurus", "https://www.cargurus.com/details/450864153",
     "All results\nLooks like that one got away\nSimilar cars to consider\n2000 BMW M\n67,444 miles\nNo Rating\n$25,412\nPrice includes fees\n"
     "Downers Grove, IL\n1998 BMW M\n28,627 miles\nNo Rating\n$30,140\nPrice includes fees\nKenosha, WI\n2000 BMW M\n53,310 miles\n", "sold"),
    (5, "cargurus", "https://www.cargurus.com/details/456811359",
     "2007 Porsche Cayman\n\nMileage: 38,700 · Hermosa Beach, CA\n\n$35,075\nFair Deal\n\n$63 Above market\n\nCheck availability\nAll results\n"
     "Share Listing\nSave this listing\n1/36\n2007 Porsche Cayman S\n\nHermosa Beach, CA\n\n$35,075\n$36,985\nPrice includes fees\nFair Deal\n", "active"),
    (7, "cargurus", "https://www.cargurus.com/details/456328720",
     "2002 BMW Z3\n\nMileage: 44,205 · Jacksonville, FL\n\n$16,690\nNo Rating\nCheck availability\nAll results\nShare Listing\nSave this listing\n"
     "1/30\n2002 BMW Z3 3.0i Roadster RWD\n\nJacksonville, FL\n\n$16,690\nPrice includes fees\nNo Rating\n(Model year too old)\n\nDealer\n", "active"),
    (216, "autotrader", "https://www.autotrader.com/cars-for-sale/vehicle/788487871",
     "Chat\nView similar vehicles\nView the Free Vehicle\nHistory Report\nRequest More\nInfo\n 22 Photos\n 1 Video\nUsed 1999 BMW Z3 M Roadster 2D RWD\n"
     "San Ramon, CA\n(77 mi away)\nView delivery details\n46,452 mi\nLow Miles\nVehicle History\nBlack Exterior\nListing Price\n$24,990\nMake Offer\n", "active"),
]


def post(path, body):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", "Origin": BASE})
    return json.load(urllib.request.urlopen(req))


def get(path):
    return json.load(urllib.request.urlopen(urllib.request.Request(BASE + path, headers={"Origin": BASE})))


post("/api/availability/start", {})
out = post("/api/availability/results", {"results": [
    {"id": lid, "detail": {"text": text, "page_url": url, "title": "", "is_detail_page": "/item/" in url or site != "facebook"}}
    for lid, site, url, text, _ in CASES]})["results"]
post("/api/availability/finish", {})
right = 0
for (lid, site, url, _, truth), r in zip(CASES, out):
    row = get(f"/api/listings/{lid}")
    ok = (r["result"] in {"sold", "unavailable"}) if truth == "sold" else r["result"] == "active"
    # a live car must stay where it was (never become a comp); a gone car must end up comp / Sold / Do not pursue
    state_ok = (row["role"] == "comp" and row["availability"] == "sold" and row["status"] in {"Sold", "Purchased"}) if truth == "sold" \
        else row["role"] != "comp" or row.get("role_user_set")
    right += ok and state_ok
    print(f"{'OK ' if ok and state_ok else 'BAD'} #{lid:<4} {site:<10} truth={truth:<6} got={r['result']:<11} -> "
          f"{row['availability']}/{row['role']}/{row['status']} verdict={(row.get('assessment') or {}).get('verdict')} · {r['evidence'][:60]}")
print(f"{right}/{len(CASES)} correct")
