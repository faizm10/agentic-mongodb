"""NovaMart demo catalog: 19 products across 6 categories."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Iterable, Literal

from .errors import ProductNotFoundError

_UNSPLASH = "https://images.unsplash.com/photos/"


@dataclass(frozen=True)
class Category:
    slug: str
    name: str
    blurb: str
    image: str


@dataclass(frozen=True)
class Product:
    id: str
    slug: str
    name: str
    category: str
    tagline: str
    description: str
    price_cents: int
    stock: int
    image: str
    rating: float
    review_count: int
    features: tuple[str, ...] = ()
    colors: tuple[str, ...] = ()
    compare_at_cents: int | None = None
    badge: str | None = None
    featured: bool = False
    tags: tuple[str, ...] = field(default=())


CATEGORIES: tuple[Category, ...] = (
    Category("audio", "Audio", "Immersive sound, beautifully engineered.", _UNSPLASH + "1505740420928-5e560c06d30e"),
    Category("wearables", "Wearables", "Timepieces and smart companions.", _UNSPLASH + "1523275335684-37898b6baf30"),
    Category("workspace", "Workspace", "Tools for focused, creative work.", _UNSPLASH + "1507473885765-e6ed057f782c"),
    Category("home", "Home", "Calm, considered objects for living.", _UNSPLASH + "1505843490538-5133c6c7d0e1"),
    Category("accessories", "Accessories", "Everyday carry, elevated.", _UNSPLASH + "1553062407-98eeb64c6a62"),
    Category("apparel", "Apparel", "Essentials made to last.", _UNSPLASH + "1618354691373-d851c5c3a990"),
)

PRODUCTS: tuple[Product, ...] = (
    Product(
        id="nm-aud-001",
        slug="aura-studio-wireless-headphones",
        name="Aura Studio Wireless Headphones",
        category="audio",
        tagline="Studio-grade sound with 40 hours of battery.",
        description=(
            "Aura Studio pairs custom 40mm beryllium drivers with adaptive noise cancellation that "
            "tunes itself to your surroundings 200 times per second. Memory-foam cushions wrapped in "
            "vegan leather keep long listening sessions comfortable."
        ),
        price_cents=24_900,
        compare_at_cents=29_900,
        stock=24,
        image=_UNSPLASH + "1505740420928-5e560c06d30e",
        rating=4.8,
        review_count=1_284,
        features=("Adaptive noise cancellation", "40-hour battery life", "Multipoint Bluetooth 5.4", "USB-C fast charge"),
        colors=("Onyx", "Sand", "Sky"),
        badge="Bestseller",
        featured=True,
        tags=("headphones", "wireless", "noise cancelling"),
    ),
    Product(
        id="nm-aud-002",
        slug="halo-open-back-headphones",
        name="Halo Open-Back Headphones",
        category="audio",
        tagline="A wide, natural soundstage for critical listening.",
        description=(
            "Hand-polished aluminium cups and open-back grilles give Halo an airy, speaker-like "
            "presentation. A detachable braided cable and replaceable pads make them a lifetime pair."
        ),
        price_cents=17_900,
        stock=12,
        image=_UNSPLASH + "1484704849700-f032a568e944",
        rating=4.6,
        review_count=412,
        features=("50mm planar drivers", "Detachable braided cable", "Replaceable velour pads", "Aluminium construction"),
        colors=("Silver", "Copper"),
        tags=("headphones", "wired", "audiophile"),
    ),
    Product(
        id="nm-aud-003",
        slug="pulse-true-wireless-earbuds",
        name="Pulse True Wireless Earbuds",
        category="audio",
        tagline="Pocketable sound with all-day comfort.",
        description=(
            "Pulse delivers rich, punchy sound in a featherweight 4.6g earbud. Three microphones per "
            "side keep your calls crisp, and the pebble-shaped case adds 30 hours of charge."
        ),
        price_cents=12_900,
        stock=40,
        image=_UNSPLASH + "1590658268037-6bf12165a8df",
        rating=4.5,
        review_count=2_031,
        features=("Active noise cancellation", "IPX5 sweat resistant", "Wireless charging case", "30 hours total playback"),
        colors=("Graphite", "Cloud"),
        badge="New",
        featured=True,
        tags=("earbuds", "wireless"),
    ),
    Product(
        id="nm-wer-001",
        slug="orbit-smartwatch-series-5",
        name="Orbit Smartwatch Series 5",
        category="wearables",
        tagline="Health insights at a glance.",
        description=(
            "An always-on sapphire display, continuous heart-rate and blood-oxygen monitoring, and "
            "seven-day battery life in a 9.8mm titanium case. Orbit learns your rhythms and nudges you "
            "toward better rest."
        ),
        price_cents=32_900,
        stock=15,
        image=_UNSPLASH + "1546868871-7041f2a55e12",
        rating=4.7,
        review_count=876,
        features=("Sapphire crystal display", "7-day battery", "Sleep & recovery coaching", "Water resistant to 50m"),
        colors=("Midnight", "Titanium"),
        featured=True,
        tags=("smartwatch", "fitness"),
    ),
    Product(
        id="nm-wer-002",
        slug="meridian-minimalist-watch",
        name="Meridian Minimalist Watch",
        category="wearables",
        tagline="Quiet design. Swiss precision.",
        description=(
            "A 38mm brushed steel case, a clean ceramic dial and a Swiss quartz movement. Meridian "
            "ships on a soft silicone strap that swaps tool-free with any 20mm band."
        ),
        price_cents=18_900,
        compare_at_cents=22_500,
        stock=8,
        image=_UNSPLASH + "1523275335684-37898b6baf30",
        rating=4.9,
        review_count=318,
        features=("Swiss quartz movement", "Ceramic dial", "Quick-release strap", "5 ATM water resistance"),
        colors=("Chalk", "Slate"),
        badge="Limited",
        featured=True,
        tags=("watch", "analog"),
    ),
    Product(
        id="nm-wsp-001",
        slug="polar-instant-film-camera",
        name="Polar Instant Film Camera",
        category="workspace",
        tagline="Analog joy, printed in seconds.",
        description=(
            "Polar brings the tactile thrill of instant film back with a sharp glass lens, automatic "
            "exposure and a built-in flash that knows when to stay quiet. Every shot is one of a kind."
        ),
        price_cents=11_900,
        stock=3,
        image=_UNSPLASH + "1526170375885-4d8ecf77b99f",
        rating=4.4,
        review_count=655,
        features=("Glass autofocus lens", "Automatic exposure", "Self-timer & double exposure", "USB-C rechargeable"),
        colors=("Arctic",),
        badge="Low stock",
        featured=True,
        tags=("camera", "film", "photography"),
    ),
    Product(
        id="nm-wsp-002",
        slug="slate-low-profile-keyboard",
        name="Slate Low-Profile Keyboard",
        category="workspace",
        tagline="Whisper-quiet keys for deep work.",
        description=(
            "Slate's scissor-switch keys travel just 1.2mm with a soft, satisfying return. Pair it with "
            "up to three devices and switch instantly with a single key."
        ),
        price_cents=9_900,
        stock=30,
        image=_UNSPLASH + "1587829741301-dc798b83add3",
        rating=4.6,
        review_count=1_102,
        features=("Scissor switches", "3-device Bluetooth", "3-month battery", "Aluminium chassis"),
        colors=("Silver", "Space Grey"),
        tags=("keyboard", "desk"),
    ),
    Product(
        id="nm-wsp-003",
        slug="lumen-articulating-task-lamp",
        name="Lumen Articulating Task Lamp",
        category="workspace",
        tagline="Glare-free light, exactly where you need it.",
        description=(
            "A powder-coated steel arm with friction hinges that hold any angle. Lumen's dimmable LED "
            "shifts from warm 2700K to daylight 5000K to match the moment."
        ),
        price_cents=8_900,
        stock=18,
        image=_UNSPLASH + "1507473885765-e6ed057f782c",
        rating=4.7,
        review_count=540,
        features=("Tunable 2700K–5000K LED", "Stepless dimming", "Friction-hinged arm", "USB-C charging port"),
        colors=("Graphite", "Bone"),
        tags=("lamp", "lighting", "desk"),
    ),
    Product(
        id="nm-hom-001",
        slug="form-molded-lounge-chair",
        name="Form Molded Lounge Chair",
        category="home",
        tagline="A mid-century icon, reimagined.",
        description=(
            "A seamless recycled-polymer shell rests on solid beech dowel legs joined with black steel. "
            "Form's gentle curve cradles you whether you're reading, working or simply pausing."
        ),
        price_cents=27_900,
        stock=4,
        image=_UNSPLASH + "1505843490538-5133c6c7d0e1",
        rating=4.8,
        review_count=207,
        features=("Recycled polymer shell", "Solid beech legs", "Felt floor protectors", "Ships fully assembled"),
        colors=("White", "Charcoal", "Sage"),
        badge="Low stock",
        featured=True,
        tags=("chair", "furniture"),
    ),
    Product(
        id="nm-hom-002",
        slug="terra-stoneware-mug-set",
        name="Terra Stoneware Mug Set",
        category="home",
        tagline="Set of four, glazed by hand.",
        description=(
            "Thrown and glazed by hand in small batches, each Terra mug holds 350ml and has a thumb-"
            "friendly handle. Dishwasher and microwave safe for everyday rituals."
        ),
        price_cents=4_200,
        stock=60,
        image=_UNSPLASH + "1514228742587-6b1558fcca3d",
        rating=4.7,
        review_count=964,
        features=("Set of four 350ml mugs", "Hand-glazed stoneware", "Dishwasher safe", "Microwave safe"),
        colors=("Porcelain", "Oat"),
        tags=("mugs", "kitchen", "ceramic"),
    ),
    Product(
        id="nm-hom-003",
        slug="verde-ceramic-planter",
        name="Verde Ceramic Planter",
        category="home",
        tagline="Soft color for your favorite green things.",
        description=(
            "A matte-glazed ceramic planter with a hidden drainage tray. Sized for succulents and small "
            "houseplants, Verde adds a calm pop of color to shelves and desks."
        ),
        price_cents=3_600,
        stock=25,
        image=_UNSPLASH + "1485955900006-10f4d324d411",
        rating=4.5,
        review_count=388,
        features=("Matte glaze", "Hidden drainage tray", "12cm diameter", "Plant not included"),
        colors=("Mint", "Blush", "Stone"),
        tags=("planter", "plants", "decor"),
    ),
    Product(
        id="nm-hom-004",
        slug="dune-stoneware-vase-trio",
        name="Dune Stoneware Vase Trio",
        category="home",
        tagline="Three silhouettes, one quiet palette.",
        description=(
            "Three slip-cast stoneware vases in graduated heights with a soft, chalky finish. Display "
            "them together or scatter them through your space."
        ),
        price_cents=6_800,
        stock=9,
        image=_UNSPLASH + "1565193566173-7a0ee3dbe261",
        rating=4.6,
        review_count=143,
        features=("Set of three", "Slip-cast stoneware", "Watertight interior", "Heights 18 / 24 / 30cm"),
        colors=("Fog",),
        tags=("vase", "decor", "ceramic"),
    ),
    Product(
        id="nm-hom-005",
        slug="ember-soy-candle",
        name="Ember Hand-Poured Soy Candle",
        category="home",
        tagline="Cedar, amber and a hint of smoke.",
        description=(
            "Ember is hand-poured with natural soy wax and a cotton wick for a clean, even 55-hour burn. "
            "Notes of cedarwood, warm amber and smoked vanilla fill a room without overwhelming it."
        ),
        price_cents=3_400,
        stock=5,
        image=_UNSPLASH + "1602874801007-bd458bb1b8b6",
        rating=4.9,
        review_count=1_520,
        features=("55-hour burn time", "Natural soy wax", "Lead-free cotton wick", "Reusable glass vessel"),
        colors=("Amber",),
        badge="Bestseller",
        featured=True,
        tags=("candle", "fragrance"),
    ),
    Product(
        id="nm-acc-001",
        slug="solstice-polarized-sunglasses",
        name="Solstice Polarized Sunglasses",
        category="accessories",
        tagline="Timeless frames, crystal-clear lenses.",
        description=(
            "Handmade acetate frames with five-barrel hinges and polarized CR-39 lenses that cut glare "
            "and block 100% of UVA/UVB light."
        ),
        price_cents=13_900,
        stock=0,
        image=_UNSPLASH + "1572635196237-14b3f281503f",
        rating=4.7,
        review_count=731,
        features=("Polarized CR-39 lenses", "100% UV protection", "Italian acetate", "Includes hard case"),
        colors=("Black",),
        tags=("sunglasses", "eyewear"),
    ),
    Product(
        id="nm-acc-002",
        slug="summit-commuter-backpack",
        name="Summit Commuter Backpack",
        category="accessories",
        tagline="Carry everything. Look like you carry nothing.",
        description=(
            "Weatherproof recycled nylon, a padded 16\" laptop sleeve and a clamshell opening that makes "
            "packing effortless. Hidden pockets keep valuables close."
        ),
        price_cents=14_900,
        stock=20,
        image=_UNSPLASH + "1553062407-98eeb64c6a62",
        rating=4.8,
        review_count=1_877,
        features=("Fits 16\" laptops", "Weatherproof recycled nylon", "Luggage pass-through", "Lifetime warranty"),
        colors=("Navy", "Black", "Olive"),
        badge="Bestseller",
        featured=True,
        tags=("backpack", "bag", "travel"),
    ),
    Product(
        id="nm-acc-003",
        slug="marais-rattan-handbag",
        name="Marais Rattan Handbag",
        category="accessories",
        tagline="Woven by artisans, finished in leather.",
        description=(
            "Each Marais bag is hand-woven from natural rattan and finished with vegetable-tanned "
            "leather straps and a brass turn-lock clasp. A summer staple that only gets better with age."
        ),
        price_cents=15_900,
        stock=6,
        image=_UNSPLASH + "1590874103328-eac38a683ce7",
        rating=4.6,
        review_count=92,
        features=("Hand-woven rattan", "Vegetable-tanned leather", "Brass hardware", "Cotton lining"),
        colors=("Natural",),
        badge="New",
        tags=("handbag", "bag"),
    ),
    Product(
        id="nm-acc-004",
        slug="glacier-insulated-bottle",
        name="Glacier Insulated Bottle",
        category="accessories",
        tagline="Cold for 24 hours. Hot for 12.",
        description=(
            "Double-wall vacuum insulated stainless steel with a powder-coated finish that won't slip. "
            "The leakproof cap is built for bags, bikes and long days."
        ),
        price_cents=3_800,
        stock=80,
        image=_UNSPLASH + "1602143407151-7111542de6e8",
        rating=4.8,
        review_count=2_406,
        features=("750ml capacity", "Double-wall vacuum insulation", "Leakproof cap", "BPA free"),
        colors=("Fern", "Snow", "Ink"),
        tags=("bottle", "outdoor"),
    ),
    Product(
        id="nm-app-001",
        slug="stride-knit-runner",
        name="Stride Knit Runner",
        category="apparel",
        tagline="Featherlight. Endlessly cushioned.",
        description=(
            "A breathable one-piece knit upper on a responsive foam midsole. Stride is built for daily "
            "miles, long commutes and everything in between."
        ),
        price_cents=13_900,
        stock=22,
        image=_UNSPLASH + "1491553895911-0055eca6402d",
        rating=4.5,
        review_count=1_045,
        features=("Recycled knit upper", "Responsive foam midsole", "Rubber outsole", "Machine washable"),
        colors=("Carbon", "Ash"),
        tags=("shoes", "running", "sneakers"),
    ),
    Product(
        id="nm-app-002",
        slug="essential-organic-tee",
        name="Essential Organic Tee",
        category="apparel",
        tagline="The perfect tee, made responsibly.",
        description=(
            "Heavyweight 220gsm organic cotton, garment-dyed for a lived-in softness from the first wear. "
            "A relaxed fit with a ribbed collar that holds its shape."
        ),
        price_cents=3_500,
        stock=100,
        image=_UNSPLASH + "1618354691373-d851c5c3a990",
        rating=4.6,
        review_count=3_218,
        features=("220gsm organic cotton", "Garment dyed", "Pre-shrunk", "Fair Trade certified"),
        colors=("Black", "White", "Heather"),
        tags=("t-shirt", "cotton"),
    ),
)

SortKey = Literal["featured", "price_asc", "price_desc", "rating", "name"]


class Catalog:
    def __init__(self, products: Iterable[Product] = PRODUCTS, categories: Iterable[Category] = CATEGORIES) -> None:
        self.products: tuple[Product, ...] = tuple(products)
        self.categories: tuple[Category, ...] = tuple(categories)
        self._by_id = {p.id: p for p in self.products}
        self._by_slug = {p.slug: p for p in self.products}

    def __len__(self) -> int:
        return len(self.products)

    def get(self, product_id_or_slug: str) -> Product:
        product = self._by_id.get(product_id_or_slug) or self._by_slug.get(product_id_or_slug)
        if product is None:
            raise ProductNotFoundError(
                f"Product '{product_id_or_slug}' does not exist.",
                details={"product_id": product_id_or_slug},
                hint="The item may have been removed from the catalog; remove it from the cart.",
            )
        return product

    def search(
        self,
        *,
        query: str | None = None,
        category: str | None = None,
        sort: SortKey = "featured",
        in_stock_only: bool = False,
        min_price_cents: int | None = None,
        max_price_cents: int | None = None,
        availability: Callable[[Product], int] | None = None,
    ) -> list[Product]:
        stock_of = availability or (lambda p: p.stock)
        results = list(self.products)
        if query:
            needle = query.strip().lower()
            results = [
                p
                for p in results
                if needle in p.name.lower()
                or needle in p.tagline.lower()
                or needle in p.category.lower()
                or any(needle in tag for tag in p.tags)
            ]
        if category and category != "all":
            results = [p for p in results if p.category == category]
        if in_stock_only:
            results = [p for p in results if stock_of(p) > 0]
        if min_price_cents is not None:
            results = [p for p in results if p.price_cents >= min_price_cents]
        if max_price_cents is not None:
            results = [p for p in results if p.price_cents <= max_price_cents]

        if sort == "price_asc":
            results.sort(key=lambda p: p.price_cents)
        elif sort == "price_desc":
            results.sort(key=lambda p: -p.price_cents)
        elif sort == "rating":
            results.sort(key=lambda p: (-p.rating, -p.review_count))
        elif sort == "name":
            results.sort(key=lambda p: p.name)
        else:
            results.sort(key=lambda p: (not p.featured, -p.review_count))
        return results
