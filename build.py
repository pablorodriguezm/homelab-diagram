#!/usr/bin/env python3
"""Render the homelab diagram (r/homelab style) from inventory.yaml.

Usage: python3 build.py [--unredacted] [--hq] [--public]
  reads inventory.local.yaml if it exists (your real data, git-ignored), else inventory.yaml
  --public     → always use the redacted inventory.yaml, output out/homelab-public.*
  → out/homelab.svg + out/homelab.png (PNG at 1.6× the SVG size)
  --unredacted → out/homelab-unredacted.*
  --hq         → also out/homelab-hq.png (4× the SVG size) and out/homelab.pdf (vector)
Content comes from inventory.yaml; the layout (positions of every box and line) is
hand-placed in this file for one specific network (UniFi + ZimaOS + Synology), so expect to move things around.
Line width encodes link speed (SPEED_W); trunks are drawn as a light cable with a dark core.
Written with Claude (Anthropic) in Claude Code, see README.md.
"""
import base64
import re
import html
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parent
PUBLIC = "--public" in sys.argv
# real data lives in inventory.local.yaml (git-ignored); the committed inventory.yaml is the redacted public one
_inv = ROOT / "inventory.local.yaml"
if PUBLIC or not _inv.exists():
    _inv = ROOT / "inventory.yaml"
INV = yaml.safe_load(_inv.read_text())
ICONS = ROOT / "icons"
OUT = ROOT / "out"

UNREDACTED = "--unredacted" in sys.argv
RED = INV["redact"]
DOMAIN = INV["meta"]["domain"]
SHOWN_DOMAIN = DOMAIN if (UNREDACTED or not RED["domain"]) else RED["domain_placeholder"]

W, H = 2400, 0   # H is computed at the end from the content

# ---------------------------------------------------------------- palette
BG = "#0b0f15"
GRID = "#151c26"
PANEL = "#111821"
PANEL2 = "#161f2b"
CHIP = "#19222e"
BORDER = "#253042"
TEXT = "#e6edf3"
MUTED = "#8d9aab"
FAINT = "#5d6a7c"
TRUNK = "#d5dde6"
TRUNK_BORDER = "#3a475a"   # border for devices on a tagged trunk (not in a single VLAN)
LINK = "#79c0ff"
REDDIT = "#ff5722"
DISK = "#1d2734"
DISK_EDGE = "#5a6b82"
PROTO = {"TCP": "#79c0ff", "UDP": "#39c5cf"}
SPEED_W = {"10G": 9.5, "2.5G": 3.8, "1G": 2.4, "100M": 1.4}   # access-line widths
VLAN = {v["key"]: v for v in INV["vlans"]}
C = {k: v["color"] for k, v in VLAN.items()}
SANS = "Fira Sans"
MONO = "Fira Code"


def trunk_w(speed):
    return SPEED_W[speed] + 3.4


def sub(s):
    """Fill {domain} placeholders and apply redaction."""
    s = str(s).replace("{domain}", SHOWN_DOMAIN)
    if not UNREDACTED and RED["domain"]:
        s = s.replace(DOMAIN, SHOWN_DOMAIN)
    return s


def esc(s):
    return html.escape(sub(s), quote=True)


def tw(s, size, mono=False):
    """Rough text width for layout decisions."""
    return len(sub(s)) * size * (0.6 if mono else 0.53)


def _font_file(pattern):
    """Path of an installed font via fontconfig (fc-match), or None."""
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", pattern], capture_output=True, text=True, timeout=5)
        return out.stdout.strip() or None
    except Exception:
        return None


try:
    from PIL import ImageFont
    _FONTS = {700: _font_file("Fira Sans:bold"), 600: _font_file("Fira Sans:semibold")}
    if not all(_FONTS.values()):
        raise RuntimeError("Fira Sans not found")
    _FCACHE = {}

    def measure(s, size, weight=700):
        key = (weight, size)
        if key not in _FCACHE:
            _FCACHE[key] = ImageFont.truetype(_FONTS[weight], size=size * 8)
        return _FCACHE[key].getlength(s) / 8
except Exception:          # no Pillow / font file: rough estimate
    def measure(s, size, weight=700):
        return tw_title(s, size)


def tw_title(s, size):
    """Width estimate for bold title text (capitals are wider than lowercase)."""
    return sum(0.68 if c.isupper() else 0.58 if c.isdigit() else 0.5 for c in s) * size


def fqdn(h):
    return f"{h}.{SHOWN_DOMAIN}"


class SVG:
    def __init__(self):
        self.p = []
        self._icon_cache = {}

    def add(self, s):
        self.p.append(s)

    def rect(self, x, y, w, h, rx=10, fill=PANEL, stroke=BORDER, sw=1.2, op=1, dash=None):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
                 f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}" opacity="{op}"{d}/>')

    def text(self, x, y, s, size=14, fill=TEXT, weight=400, anchor="start", mono=False, ls=0, op=1):
        fam = MONO if mono else SANS
        lsa = f' letter-spacing="{ls}"' if ls else ""
        self.add(f'<text x="{x:.1f}" y="{y:.1f}" font-family="{fam}" font-size="{size}" fill="{fill}" '
                 f'font-weight="{weight}" text-anchor="{anchor}" opacity="{op}"{lsa}>{esc(s)}</text>')

    def icon(self, name, x, y, size):
        if name not in self._icon_cache:
            f = ICONS / f"{name}.svg"
            if not f.exists():
                raise SystemExit(f"missing icon '{name}' – run: python3 fetch_icons.py")
            self._icon_cache[name] = base64.b64encode(f.read_bytes()).decode()
        self.add(f'<image x="{x:.1f}" y="{y:.1f}" width="{size}" height="{size}" '
                 f'href="data:image/svg+xml;base64,{self._icon_cache[name]}"/>')

    def line(self, pts, color, sw=3, dash=None, r=10, op=1, cap="round"):
        self.add(f'<path d="{rounded(pts, r)}" fill="none" stroke="{color}" stroke-width="{sw}" '
                 f'stroke-linecap="{cap}" stroke-linejoin="round" opacity="{op}"'
                 + (f' stroke-dasharray="{dash}"' if dash else "") + "/>")

    def trunk(self, pts, r=12, sw=7):
        """Tagged trunk: light cable with a dark core."""
        self.line(pts, TRUNK, sw=sw, r=r)
        self.line(pts, BG, sw=sw - 4.4, r=r)

    def arrow(self, pts, color=MUTED, sw=1.8, dash=None, r=8):
        """Line with an arrowhead at the last point."""
        self.line(pts, color, sw, dash=dash, r=r)
        (x0, y0), (x1, y1) = pts[-2], pts[-1]
        dx, dy = x1 - x0, y1 - y0
        ln = max(abs(dx), abs(dy)) or 1
        ux, uy = dx / ln, dy / ln
        a, b = 8, 4.5
        p1 = (x1 - ux * a - uy * b, y1 - uy * a + ux * b)
        p2 = (x1 - ux * a + uy * b, y1 - uy * a - ux * b)
        self.add(f'<path d="M{x1},{y1} L{p1[0]:.1f},{p1[1]:.1f} L{p2[0]:.1f},{p2[1]:.1f} Z" fill="{color}"/>')

    def pill(self, x, y, s, color, size=12, fill=None, mono=False, anchor="start", h=None, pad=8, tcolor=None, weight=600):
        w = tw(s, size, mono) + pad * 2
        h = h or size + 10
        if anchor == "middle":
            x -= w / 2
        elif anchor == "end":
            x -= w
        self.rect(x, y, w, h, rx=h / 2, fill=fill or color + "22", stroke=color + "88", sw=1)
        self.text(x + w / 2, y + h / 2 + size * 0.36, s, size, tcolor or color, weight, "middle", mono)
        return w

    def label(self, x, y, s, color=MUTED, size=12, anchor="middle", mono=False, bg=BG):
        """Text on a line, with a background knock-out."""
        w = tw(s, size, mono) + 12
        bx = x - w / 2 if anchor == "middle" else (x if anchor == "start" else x - w)
        self.rect(bx, y - size - 2, w, size + 9, rx=5, fill=bg, stroke=bg, sw=0)
        self.text(bx + w / 2, y + 1, s, size, color, 500, "middle", mono)

    def render(self):
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W}" height="{H}" viewBox="0 0 {W} {H}">'
                + "".join(self.p) + "</svg>")


def rounded(pts, r):
    """Orthogonal polyline with rounded corners."""
    if len(pts) == 2:
        return f"M{pts[0][0]},{pts[0][1]} L{pts[1][0]},{pts[1][1]}"
    d = f"M{pts[0][0]},{pts[0][1]}"
    for i in range(1, len(pts) - 1):
        (x0, y0), (x1, y1), (x2, y2) = pts[i - 1], pts[i], pts[i + 1]
        l1 = max(abs(x1 - x0), abs(y1 - y0))
        l2 = max(abs(x2 - x1), abs(y2 - y1))
        rr = min(r, l1 / 2, l2 / 2)
        ax = x1 - (x1 - x0) / l1 * rr if l1 else x1
        ay = y1 - (y1 - y0) / l1 * rr if l1 else y1
        bx = x1 + (x2 - x1) / l2 * rr if l2 else x1
        by = y1 + (y2 - y1) / l2 * rr if l2 else y1
        d += f" L{ax:.1f},{ay:.1f} Q{x1},{y1} {bx:.1f},{by:.1f}"
    d += f" L{pts[-1][0]},{pts[-1][1]}"
    return d


S = SVG()


# ---------------------------------------------------------------- shared pieces
def hostname(g):
    """(text, color) for the second address line: public host, or <name>.internal."""
    if g.get("host"):
        return fqdn(g["host"]), LINK
    if g.get("internal"):
        return g["internal"], None
    return None, None


def addr(x, y, ip, g, color, size=12):
    """Address line under a card's name (like the switch): IP · hostname, in the VLAN color
    (a public Traefik hostname is blue)."""
    S.text(x, y, ip, size, color, 500, mono=True)
    name, ncol = hostname(g)
    if name:
        cw_ = size * 0.6                      # Fira Code advance width
        dx = x + len(ip) * cw_ + cw_ * 0.6
        S.text(dx, y, "·", size, MUTED, 400, mono=True)
        S.text(dx + cw_ * 1.6, y, name, size, ncol or color, 400, mono=True, op=1 if ncol else 0.85)


def card(x, y, w, h, color=None, title=None, icons=None, sub_=None, isz=34, tsize=19):
    """Panel with a colored left accent, icons, title and subtitle."""
    S.rect(x, y, w, h, rx=14, fill=PANEL, stroke=(color + "aa") if color else BORDER, sw=1.6)
    if color:
        S.add(f'<rect x="{x+1}" y="{y+14}" width="4" height="{h-28}" rx="2" fill="{color}"/>')
    ix = x + 18
    for ic in icons or []:
        S.icon(ic, ix, y + 16, isz)
        ix += isz + 6
    tx_ = ix + 4 if icons else x + 18
    if title:
        S.text(tx_, y + 16 + isz * 0.5 - (2 if sub_ else -6), title, tsize, TEXT, 600)
    if sub_:
        S.text(tx_, y + 16 + isz * 0.5 + 17, sub_, 13, MUTED, 400)
    return tx_


HWC = "#c08a5c"   # "physical hardware" accent (same tan as the HARDWARE labels)


def hw_bar(x, y, h):
    """Left accent bar = this card is a physical box / hardware part."""
    S.add(f'<rect x="{x+1}" y="{y+12}" width="4" height="{h-24}" rx="2" fill="{HWC}"/>')


TAB = 34   # height of the folder tab on physical-device cards


def tab_width(g):
    """Tab fits 'name · type' (name 20px bold, type 14px semibold), measured from the font."""
    return 16 + measure(g["name"], 20, 700) + 7 + measure("·", 20, 700) + 7 + measure(g["type"], 14, 600) + 18


def folder(x, y, w, h, tab_w, fill, stroke, sw):
    """Card body at (x, y, w, h) with a folder tab on its top-left edge, tab_w wide and TAB high."""
    t = y - TAB
    r, rt, sl = 12, 9, 16
    d = (f"M{x},{y+h-r} L{x},{t+rt} Q{x},{t} {x+rt},{t} L{x+tab_w-sl:.1f},{t} "
         f"C{x+tab_w-sl/3:.1f},{t} {x+tab_w-sl/3:.1f},{y} {x+tab_w+sl/2:.1f},{y} "
         f"L{x+w-r},{y} Q{x+w},{y} {x+w},{y+r} L{x+w},{y+h-r} Q{x+w},{y+h} {x+w-r},{y+h} "
         f"L{x+r},{y+h} Q{x},{y+h} {x},{y+h-r} Z")
    S.add(f'<path d="{d}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"/>')


def device_card(x, y, w, h, icons, g, color, fill=PANEL, stroke=None, sw=1.6):
    """Physical device: folder tab 'name · type'; body = icon, full device name, IP · hostname, OS."""
    folder(x, y, w, h, tab_width(g), fill, stroke or color + "aa", sw)
    S.add(f'<text x="{x+16}" y="{y-10}" font-family="{SANS}" fill="{TEXT}">'
          f'<tspan font-size="20" font-weight="700">{esc(g["name"])}</tspan>'
          f'<tspan dx="7" font-size="20" font-weight="700" fill-opacity="0.6">·</tspan>'
          f'<tspan dx="7" font-size="14" font-weight="600">{esc(g["type"])}</tspan></text>')
    ix = x + 14
    for ic in icons:                      # icon spans the three body lines (full name … OS)
        S.icon(ic, ix, y + 15, 54)
        ix += 58
    tx_ = ix + 8
    dy = (0 if g.get("ip") else 8) if g.get("os") else (8 if g.get("ip") else 18)         # no OS line → center the two lines on the icon
    S.text(tx_, y + 28 + dy, g.get("model") or "custom build", 14, TEXT if g.get("model") else MUTED, 500)
    if g.get("ip"):
        addr(tx_, y + 48 + dy, g["ip"], g, color)
    if g.get("os"):
        S.text(tx_, y + (66 if g.get("ip") else 56), g["os"], 12, MUTED, 400)
    if g.get("config"):                   # optional 4th line: how the device is set up
        S.text(tx_, y + 84, g["config"], 12, FAINT, 400)


def jack(x, y, w, h, fill, stroke, sfp=False):
    S.rect(x, y, w, h, rx=5, fill=fill, stroke=stroke, sw=1.4)
    if sfp:
        S.rect(x + 8, y + 9, w - 16, h - 18, rx=2, fill="#0b0f15", stroke="none", sw=0)
    else:
        S.add(f'<path d="M{x+10},{y+h-8} v-14 h6 v-5 h{w-32} v5 h6 v14 z" fill="#0b0f15"/>')


def ticks(cx, y, untagged=None, tagged=(), up=False):
    """VLAN indicator bars for a port: tall = untagged, short = tagged. up=True grows upwards."""
    vk = ([untagged] if untagged else []) + list(tagged)
    x0 = cx - (len(vk) * 7) / 2
    for j, k in enumerate(vk):
        hh = 8 if k == untagged else 5
        yy = y - hh if up else y
        S.add(f'<rect x="{x0 + j*7:.1f}" y="{yy}" width="5" height="{hh}" rx="1" fill="{C[k]}"/>')


def vlan_tag(vkey):
    v = VLAN[vkey]
    return f"{v['tag']} · {v['name']}" if v["tag"].startswith("VLAN") else v["name"]




# ---------------------------------------------------------------- background + header
S.add(f'<defs><pattern id="dots" width="24" height="24" patternUnits="userSpaceOnUse">'
      f'<circle cx="1.5" cy="1.5" r="1.3" fill="{GRID}"/></pattern></defs>')
S.add(f'<rect x="0" y="0" width="100%" height="100%" fill="{BG}"/>')      # canvas height is set at the end
S.add('<rect x="0" y="0" width="100%" height="100%" fill="url(#dots)"/>')

meta = INV["meta"]
title = meta["title"] if (UNREDACTED or not RED["domain"]) else meta["title_redacted"]
S.text(40, 86, title, 46, TEXT, 700, ls=-0.5)
S.text(W - 40, 74, meta["watermark"], 21, REDDIT, 700, "end")
S.text(W - 40, 100, meta["date"], 16, MUTED, 400, "end")

POE = "#e3b341"            # PoE: port number in amber = this port powers the device
PLC_C = "#e3b341"          # powerline (through the mains wiring) = dashed amber
ALL_VLANS = ["1", "25", "50"]


def speed_tag(x, y, txt, color, anchor="start"):
    """Small speed label where a cable enters a device."""
    S.label(x, y, txt, color, 11, anchor=anchor, mono=True)


# ================================================================ TOP: edge chain
CX = 1300                                   # internet → ONT → UCG WAN all on this x
cy0 = 172
fy, fh = 310, 86                            # ONT (body; tab above)
ox, oy, ow, oh = 860, 470, 900, 320         # UCG Ultra (hardware left, services right, ports bottom right)
bx, by, bw, bh = 580, 880, 1180, 112        # USW-Lite-16-PoE
SB = by + bh
PX0, PITCH = bx + 350, 50


def port_x(i):
    """Center x of switch port i (0-15, one row of RJ45)."""
    return PX0 + i * PITCH + 22


# Remote access: reaches home over the internet (dotted = logical, not a cable).
rw = INV["remote"]
rx_, ry_ = 600, 150
S.line([(rx_ + 290, ry_ + 42), (CX - 150, ry_ + 42)], C["vpn"], 1.8, dash="2 5", r=0)

# Internet cloud (circles: outline trick)
blobs = [(-95, 18, 42), (-45, -8, 56), (25, -18, 62), (90, 6, 48), (130, 26, 30), (-130, 30, 28)]
for dx, dy, r in blobs:
    S.add(f'<circle cx="{CX+dx}" cy="{cy0+dy}" r="{r+2.5}" fill="#3a4a60"/>')
S.add(f'<rect x="{CX-150}" y="{cy0+12}" width="300" height="46" rx="23" fill="#3a4a60"/>')
for dx, dy, r in blobs:
    S.add(f'<circle cx="{CX+dx}" cy="{cy0+dy}" r="{r}" fill="#1a2433"/>')
S.add(f'<rect x="{CX-147.5}" y="{cy0+14.5}" width="295" height="41" rx="20.5" fill="#1a2433"/>')
S.text(CX, cy0 + 14, "Internet", 26, TEXT, 600, "middle")
S.text(CX, cy0 + 40, INV["internet"]["note"], 13, MUTED, 400, "middle")

S.rect(rx_, ry_, 290, 84, rx=12, fill=PANEL, stroke=C["vpn"] + "aa", sw=1.6)
S.icon("wireguard", rx_ + 16, ry_ + 22, 38)
S.text(rx_ + 66, ry_ + 38, rw["name"], 18, TEXT, 600)
S.text(rx_ + 66, ry_ + 60, rw["note"], 13, MUTED, 400)

# ONT: fibre in on top, Ethernet out to the UCG WAN
ont = INV["ont"]
ISPD = INV["internet"]["speed"]
OTX = CX - 260
S.line([(CX, cy0 + 58), (CX, fy + 12)], "#3fb950", 2.2, dash="7 3")          # fibre
S.label(CX - 10, (cy0 + 58 + fy - TAB) / 2 + 12, "↓ " + ISPD["down"], "#c3cdd9", 12, anchor="end", mono=True)
S.label(CX + 10, (cy0 + 58 + fy - TAB) / 2 + 12, "↑ " + ISPD["up"], "#c3cdd9", 12, anchor="start", mono=True)
device_card(OTX, fy, 340, fh, ["glyph-ont"], ont, C["wan"])
S.rect(CX - 14, fy + 12, 28, 24, rx=4, fill="#1e3a28", stroke="#3fb950", sw=1.4)   # SC/APC fibre port
S.rect(CX - 5, fy + 18, 10, 12, rx=1.5, fill="#3fb950", stroke="none", sw=0)
S.text(CX + 22, fy + 29, "fibra", 11, "#3fb950", 500, mono=True)

# UCG Ultra card
gwc = INV["gateway"]
WANX = CX
S.line([(WANX, fy + fh), (WANX, oy + 30)], C["wan"], SPEED_W["1G"])
device_card(ox, oy, ow, oh, ["unifi"], gwc, C["1"], stroke=TRUNK_BORDER)
S.text(ox + 20, oy + 96, "HARDWARE", 11.5, HWC, 700, ls=1.4)
SVX = ox + 244
S.text(SVX + 2, oy + 96, "SERVICIOS", 11.5, HWC, 700, ls=1.4)
for i, hwi in enumerate(gwc["hw"]):
    x, y = ox + 16, oy + 106 + i * 64
    S.rect(x, y, 200, 58, rx=11, fill="#151d29", stroke="#2e3a4c", sw=1.2)
    hw_bar(x, y, 58)
    S.icon(hwi["icon"], x + 12, y + 15, 28)
    S.text(x + 50, y + 17, hwi["label"], 10, HWC, 700, ls=1.3)
    S.text(x + 50, y + 35, hwi["value"], 15.5, TEXT, 700)
    S.text(x + 50, y + 50, hwi["sub"], 10.5, MUTED, 400)
sw_, sh_ = 190, 50
for i, sv in enumerate(gwc["services"]):
    col, row = i % 3, i // 3
    x = SVX + col * (sw_ + 9)
    y = oy + 106 + row * (sh_ + 8)
    S.rect(x, y, sw_, sh_, rx=9, fill=CHIP, stroke=BORDER, sw=1)
    S.icon(sv["icon"], x + 9, y + 12, 26)
    S.text(x + 44, y + 22, sv["name"], 14, TEXT, 600)
    S.text(x + 44, y + 39, sv["note"], 11.5, MUTED, 400)

# UCG ports: WAN on top (from the ONT), LAN 1-4 along the bottom edge; LAN 1 sits right above switch port 16
jack(WANX - 24, oy + 14, 48, 36, C["wan"] + "55", C["wan"])
ticks(WANX, oy + 55, untagged="wan")
S.text(WANX + 34, oy + 30, "WAN", 11, MUTED, 500, mono=True)
S.text(WANX + 34, oy + 45, "puerto 5", 10.5, FAINT, 500)
speed_tag(WANX + 6, (fy + fh + oy + 14) / 2 + 4, "1G", C["wan"])
GWP = {}
lan_y = oy + oh - 46
for i, p in enumerate(gwc["ports"][:4]):
    x = port_x(15) - i * 52
    GWP[p["name"]] = x
    used = p["to"] != "libre"
    unt, tg = p.get("untagged"), p.get("tagged", [])
    if not used:
        fill, stroke = "#1b222c", "#3a475a"
    elif tg:
        fill, stroke = "#2a3443", TRUNK
    else:
        fill, stroke = C[unt] + "55", C[unt]
    S.text(x, lan_y - 18 if used else lan_y - 7, p["name"], 11, MUTED, 500, "middle", mono=True)
    if used:
        ticks(x, lan_y - 5, unt, tg, up=True)
GW_LANX = port_x(15) - 3 * 52 - 40
S.text(GW_LANX, lan_y + 23, "LAN", 11, MUTED, 600, "end", mono=True)

# cables first, jacks on top
PLX, PLY, PLW, PLH = 580, 690, 268, 86           # PLC card
TSY = 520                                         # the basement end of the powerline
S.trunk([(GWP["1"], lan_y + 20), (GWP["1"], by)], sw=trunk_w("1G"))
PLC_IN = PLX + PLW - 40
S.line([(GWP["2"], lan_y + 20), (GWP["2"], by - 45), (PLC_IN, by - 45), (PLC_IN, PLY + PLH)], C["1"], SPEED_W["1G"])
for i, p in enumerate(gwc["ports"][:4]):
    x = GWP[p["name"]]
    used = p["to"] != "libre"
    unt, tg = p.get("untagged"), p.get("tagged", [])
    if not used:
        jack(x - 22, lan_y, 44, 36, "#1b222c", "#3a475a")
    elif tg:
        jack(x - 22, lan_y, 44, 36, "#2a3443", TRUNK)
    else:
        jack(x - 22, lan_y, 44, 36, C[unt] + "55", C[unt])
speed_tag(GWP["1"] + 8, (oy + oh + by) / 2 + 22, "1G", TRUNK)
speed_tag(PLC_IN + 8, PLY + PLH + 26, "1G", C["1"])

# PLC (powerline) and what hangs off it in the basement
plc = INV["plc"]
device_card(PLX, PLY, PLW, PLH, ["glyph-plc"], plc, C["1"])
device_card(PLX, TSY, PLW, PLH, ["glyph-evse"], INV["basement"], PLC_C)
pl_x = PLX + PLW - 40
S.line([(pl_x, PLY - TAB + 2), (pl_x, TSY + PLH)], PLC_C, 2.2, dash="6 4", cap="butt")
S.label(pl_x - 8, (TSY + PLH + PLY - TAB) / 2 + 4, "powerline", PLC_C, 11, anchor="end", mono=True)

# ================================================================ switch
swc = INV["switch"]
device_card(bx, by, bw, bh, ["unifi"], swc, C["1"], fill="#141b25", stroke=TRUNK_BORDER)
# PoE+ group marker over ports 1-8
px_a, px_b = port_x(0) - 22, port_x(7) + 22
S.line([(px_a, by + 9), (px_b, by + 9)], POE + "88", 1.2, r=0, cap="butt")
S.label((px_a + px_b) / 2, by + 13, "PoE+ · 1–8", POE, 10, bg="#141b25")

PORTS = {}
for i, p in enumerate(swc["ports"]):
    x = port_x(i) - 22
    y = by + 30
    unt = p.get("untagged")
    tagged = p.get("tagged", [])
    free = p["to"] == "libre"
    if free:
        fill, stroke = "#1b222c", "#3a475a"
    elif unt and not tagged:
        fill, stroke = C[unt] + "55", C[unt]
    else:
        fill, stroke = "#2a3443", TRUNK
    jack(x, y, 44, 36, fill, stroke)
    S.text(port_x(i), y - 7, p["name"], 11, POE if p.get("poe") else MUTED, 600 if p.get("poe") else 500, "middle", mono=True)
    if not free:
        ticks(port_x(i), y + 41, unt, tagged)
    S.text(port_x(i), by + bh - 9, p["to"], 10, FAINT if free else MUTED, 500, "middle")
    PORTS[p["name"]] = port_x(i)

# ================================================================ device row
DY = SB + 140
dev = INV["devices"]
HA = (40, 400, 86)
AP = (460, 400, 160)
FLX = (880, 510, 112)
ATV = (1410, 400, 86)
SYN_X = 1700                    # Synology card (bottom right)
SHL = (1920, 440, 86)


def device(key, box):
    x, w, h = box
    d = dev[key]
    device_card(x, DY if len(box) == 3 else box[3], w, h, d["icons"], d, C[d["vlan"]],
                stroke=TRUNK_BORDER if d.get("trunk") else None)
    return x, w, h


device("ha", HA)
x, w, h = device("ap", AP)
S.text(x + 18, DY + 96, "SSIDs", 11, FAINT, 600, ls=1.2)
for i, s in enumerate(INV["ssids"]):
    name = s["redacted"] if (RED["ssid_names"] and not UNREDACTED) else s["name"]
    col = C[s["vlan"]]
    sy = DY + 106 + i * 17
    S.add(f'<circle cx="{x+24}" cy="{sy+5}" r="4" fill="{col}"/>')
    S.text(x + 36, sy + 9, name, 13, TEXT, 500)
    S.text(x + 190, sy + 9, vlan_tag(s["vlan"]), 12, col, 500, mono=True)
device("appletv", ATV)
device("shelly", SHL)

# USW Flex with its own five ports; port 1 sits right under switch port 6 (straight PoE uplink)
device("flex", FLX)
FP = {}
for i, p in enumerate(INV["flex_ports"]):
    x = PORTS["6"] + i * 40
    FP[p["name"]] = x
    S.text(x, DY + 23, p["name"], 10.5, POE if i == 0 else MUTED, 600 if i == 0 else 500, "middle", mono=True)
    S.text(x, DY + FLX[2] - 9, p["to"], 9.5, FAINT if p["to"] == "libre" else MUTED, 500, "middle")

# switch → devices. Left-going cables: higher port = deeper; right-going: lower port = deeper (no crossings)
HA_IN = HA[0] + tab_width(dev["ha"]) + 40
AP_IN = AP[0] + tab_width(dev["ap"]) + 30
ATV_IN = ATV[0] + tab_width(dev["appletv"]) + 40
SHL_IN = SHL[0] + tab_width(dev["shelly"]) + 40
SYN_IN = SYN_X + tab_width(INV["nas"]) + 30
assert HA_IN < HA[0] + HA[1] - 10 and AP_IN < AP[0] + AP[1] - 10 and SHL_IN < SHL[0] + SHL[1] - 10
assert ATV[0] + ATV[1] < SYN_IN - 20 < SHL[0] - 20, "Synology cable must pass between Apple TV and Shelly"
S.line([(PORTS["3"], SB), (PORTS["3"], SB + 30), (HA_IN, SB + 30), (HA_IN, DY)], C["1"], SPEED_W["1G"])
S.trunk([(PORTS["5"], SB), (PORTS["5"], SB + 50), (AP_IN, SB + 50), (AP_IN, DY)], sw=trunk_w("1G"))
S.trunk([(PORTS["6"], SB), (PORTS["6"], DY + 30)], sw=trunk_w("1G"))
S.line([(PORTS["7"], SB), (PORTS["7"], SB + 70), (ATV_IN, SB + 70), (ATV_IN, DY)], C["1"], SPEED_W["1G"])
S.line([(PORTS["14"], SB), (PORTS["14"], SB + 30), (SHL_IN, SB + 30), (SHL_IN, DY)], C["1"], SPEED_W["100M"])

ST = DY - 9
speed_tag(HA_IN + 6, ST, "1G", C["1"])
speed_tag(AP_IN + 8, ST, "1G", TRUNK)
speed_tag(PORTS["6"] + 10, DY - 9, "1G", TRUNK)
speed_tag(ATV_IN + 6, ST, "1G", C["1"])
speed_tag(SHL_IN + 6, ST, "100M", C["1"])

# ---- second row: what hangs off the Flex
DY2 = DY + 208
KVM = (1262, 240, 86)
LAP = (1515, 330, 86)
device_card(KVM[0], DY2, KVM[1], KVM[2], dev["jetkvm"]["icons"], dev["jetkvm"], C["1"])
device_card(LAP[0], DY2, LAP[1], LAP[2], dev["laptop"]["icons"], dev["laptop"], C["1"])
KVM_IN = KVM[0] + tab_width(dev["jetkvm"]) + 22
LAP_IN = LAP[0] + tab_width(dev["laptop"]) + 40
assert KVM_IN < KVM[0] + KVM[1] - 8 and LAP_IN < LAP[0] + LAP[1] - 8 and LAP[0] + LAP[1] < SYN_IN - 20
FB = DY + FLX[2]
S.line([(FP["3"], FB), (FP["3"], FB + 48), (KVM_IN, FB + 48), (KVM_IN, DY2)], C["1"], SPEED_W["100M"])
S.line([(FP["4"], FB), (FP["4"], FB + 26), (LAP_IN, FB + 26), (LAP_IN, DY2)], C["1"], SPEED_W["1G"], dash="10 5", cap="butt")
speed_tag(KVM_IN + 6, DY2 - 9, "100M", C["1"])
speed_tag(LAP_IN + 6, DY2 - 9, "1G · a ratos", C["1"])

# Flex jacks (drawn after the cables so the cable ends sit under them)
for i, p in enumerate(INV["flex_ports"]):
    x = FP[p["name"]]
    if i == 0:
        fill, stroke = "#2a3443", TRUNK
    elif p["to"] == "libre":
        fill, stroke = "#1b222c", "#3a475a"
    else:
        fill, stroke = C[p["untagged"]] + "55", C[p["untagged"]]
    jack(x - 17, DY + 30, 34, 32, fill, stroke)
    if i == 0:
        ticks(x, DY + 67, "1", ["25", "50"])
    elif p["to"] != "libre":
        ticks(x, DY + 67, p["untagged"])

# ================================================================ open to the internet (info card, right column)
ix_, iy_, iw_ = 1790, 150, 570
pf = INV["port_forwards"]
ih_ = 84 + len(pf) * 40
HOSTS = INV["hosts"]
S.rect(ix_, iy_, iw_, ih_, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(ix_ + 20, iy_ + 34, "Abierto a internet", 19, TEXT, 600)
S.text(ix_ + 20, iy_ + 56, f"port forwards en el UCG Ultra · {sum(not r.get('disabled') for r in pf)} activos, "
       f"{sum(bool(r.get('disabled')) for r in pf)} desactivados", 12.5, MUTED, 400)
for i, r in enumerate(pf):
    y = iy_ + 72 + i * 40
    op = 0.45 if r.get("disabled") else 1
    S.add(f'<g opacity="{op}">')
    S.rect(ix_ + 14, y, iw_ - 28, 34, rx=8, fill=CHIP, stroke=BORDER, sw=1)
    S.text(ix_ + 26, y + 22, r["port"], 13, TEXT, 600, mono=True)
    S.text(ix_ + 100, y + 15, r["to"], 13, TEXT, 600)
    ip, _, port = r["dest"].partition(":")
    S.text(ix_ + 100, y + 29, f"→ {HOSTS.get(ip, ip)} · {r['dest']}", 11, MUTED, 400, mono=True)
    px = ix_ + iw_ - 24
    if r.get("disabled"):
        S.text(px, y + 22, "desactivada", 11.5, MUTED, 500, "end")
    else:
        for pr in reversed(r["proto"].split("/")):
            px -= S.pill(px, y + 7, pr, PROTO[pr], 11, mono=True, anchor="end", pad=7, h=20) + 6
    S.add("</g>")
assert iy_ + ih_ < SB + 30 - 20, "port-forward card runs into the Shelly cable"

# ================================================================ legend (left column, under the Networks card)
LG_ROWS = ["trunk", "access", "wan", "fiber", "plc", "kvm", "net", "speed", "ticks", "poe", "docker"]
vx, vy, vw = 40, 150, 500
vh = 70 + len(INV["vlans"]) * 60
lgx, lgy, lgw = 40, vy + vh + 20, 500
LR = 27
lgh = 52 + len(LG_ROWS) * LR + 8
S.rect(lgx, lgy, lgw, lgh, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(lgx + 20, lgy + 36, "Leyenda", 19, TEXT, 600)
SX, TX = lgx + 22, lgx + 162          # symbol column, text column
for i, k in enumerate(LG_ROWS):
    y = lgy + 66 + i * LR
    t = None
    if k == "trunk":
        S.trunk([(SX, y), (SX + 50, y)], sw=trunk_w("1G"), r=0); t = "trunk 802.1Q (VLANs etiquetadas)"
    elif k == "access":
        for jj, c in enumerate(["1", "25", "50"]):
            S.line([(SX + jj * 17, y), (SX + jj * 17 + 12, y)], C[c], 3.2, r=0, cap="butt")
        t = "puerto de acceso, color de su VLAN"
    elif k == "wan":
        S.line([(SX, y), (SX + 50, y)], C["wan"], 3, r=0); t = "WAN (ONT → UCG)"
    elif k == "fiber":
        S.line([(SX, y), (SX + 50, y)], "#3fb950", 2.2, dash="7 3", r=0, cap="butt"); t = "fibra óptica de DIGI"
    elif k == "plc":
        S.line([(SX, y), (SX + 50, y)], PLC_C, 2.2, dash="6 4", r=0, cap="butt"); t = "powerline (por la red eléctrica)"
    elif k == "kvm":
        S.line([(SX, y), (SX + 50, y)], MUTED, 2, dash="5 5", r=0, cap="butt"); t = "consola KVM (HDMI + USB)"
    elif k == "net":
        S.line([(SX, y), (SX + 50, y)], MUTED, 1.8, dash="2 5", r=0); t = "por internet (no es un cable)"
    elif k == "speed":
        S.text(SX, y + 4.5, "grosor = velocidad", 12.5, MUTED, 400)
        x0 = TX
        for lbl in ("1G", "100M"):
            S.line([(x0, y), (x0 + 26, y)], C["1"], SPEED_W[lbl], r=0, cap="butt")
            S.text(x0 + 32, y + 4.5, lbl, 12, TEXT, 500, mono=True)
            x0 += 32 + tw(lbl, 12, True) + 20
        S.line([(x0, y), (x0 + 26, y)], C["1"], SPEED_W["1G"], dash="10 5", r=0, cap="butt")
        S.text(x0 + 32, y + 4.5, "a ratos", 12, TEXT, 500)
    elif k == "ticks":
        for jj, c in enumerate(["1", "25", "50"]):
            S.add(f'<rect x="{SX + jj*7}" y="{y-5 if jj == 0 else y-2}" width="5" height="{8 if jj == 0 else 5}" rx="1" fill="{C[c]}"/>')
        t = "VLANs del puerto (alta = nativa)"
    elif k == "poe":
        S.text(SX + 4, y + 4.5, "3", 12, POE, 600, mono=True); t = "puerto que alimenta por PoE"
    elif k == "docker":
        S.add(f'<circle cx="{SX+12}" cy="{y}" r="11" fill="{PANEL}" stroke="#2f6fb0" stroke-width="1.2"/>')
        S.icon("docker", SX + 4, y - 8, 16); t = "stack de Docker Compose"
    if t:
        S.text(TX, y + 4.5, t, 13, MUTED, 400)

# ================================================================ left column: VLAN table
S.rect(vx, vy, vw, vh, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(vx + 20, vy + 36, "Redes", 19, TEXT, 600)
for i, v in enumerate(INV["vlans"]):
    y = vy + 56 + i * 60
    S.rect(vx + 14, y, vw - 28, 52, rx=9, fill=CHIP, stroke=v["color"] + "55", sw=1)
    S.add(f'<rect x="{vx+14}" y="{y}" width="6" height="52" rx="3" fill="{v["color"]}"/>')
    S.text(vx + 32, y + 22, v["tag"], 13, v["color"], 600, mono=True)
    S.text(vx + 128, y + 22, v["name"], 15.5, TEXT, 600)
    S.text(vx + vw - 28, y + 22, v["subnet"], 13.5, TEXT if "/" in v["subnet"] else MUTED, 500, "end", mono=True)
    S.text(vx + 32, y + 42, v["note"], 12, MUTED, 400)
assert lgy + lgh < by - TAB - 10 or lgx + lgw < bx, "legend overlaps the switch"

# ================================================================ energy panel (left, second row)
EX, EY, EW = 40, DY2 - TAB, 1170
EH = 170
S.rect(EX, EY, EW, EH, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(EX + 20, EY + 32, "Energía y coche", 17, TEXT, 600)
S.text(EX + 20, EY + 52, "de dónde salen los datos de consumo y del Tesla", 12.5, MUTED, 400)
assert EY > DY + AP[2] + 12, "energy panel overlaps the access point"
assert EX + EW < KVM[0] - 20 and EX + EW < FP["2"] - 20
ENW, ENH = 240, 64
eny = EY + 78
egap = (EW - 28 - 4 * ENW) / 3
enodes = [
    ("glyph-car", "Tesla Model Y", "Juniper · nube de Tesla"),
    ("teslamate", "TeslaMate", "Docker en ZimaOS"),
    ("homeassistant", "Home Assistant", "Raspberry Pi 4"),
    ("shelly", "Shelly Pro 3EM", "cuadro eléctrico"),
]
for k, (ic, nm, sb_) in enumerate(enodes):
    x = EX + 14 + k * (ENW + egap)
    S.rect(x, eny, ENW, ENH, rx=9, fill=CHIP, stroke=BORDER, sw=1)
    S.icon(ic, x + 12, eny + 16, 32)
    S.text(x + 54, eny + 28, nm, 14, TEXT, 600)
    S.text(x + 54, eny + 46, sb_, 11.5, MUTED, 400)


def enx(k):
    return EX + 14 + k * (ENW + egap)


my = eny + ENH / 2
S.line([(enx(0) + ENW + 4, my), (enx(1) - 12, my)], MUTED, 1.8, dash="2 5", r=0)
S.arrow([(enx(1) - 14, my), (enx(1) - 4, my)])
S.label((enx(0) + ENW + enx(1)) / 2, my - 10, "API de Tesla", MUTED, 11)
S.arrow([(enx(1) + ENW + 4, my), (enx(2) - 4, my)])
S.label((enx(1) + ENW + enx(2)) / 2, my - 10, "MQTT", MUTED, 11)
S.arrow([(enx(3) - 4, my), (enx(2) + ENW + 4, my)])
S.label((enx(2) + ENW + enx(3)) / 2, my - 10, "consumo", MUTED, 11)
S.text(enx(1) + 12, eny + ENH + 20, "+ Grafana: viajes, cargas, batería", 11.5, FAINT, 400)

# ================================================================ clients card (right, under the Shelly)
def n_items(it):
    m = re.match(r"(\d+)×", it)
    return int(m.group(1)) if m else 1


CLX, CLY, CLW = SHL[0], DY + SHL[2] + 26, W - 40 - SHL[0]
assert CLX > SYN_IN + 20, "clients card collides with the Synology cable"
CL_LW = 92                                   # label column
CL_TW = CLW - 28 - CL_LW                     # text column
cl_lines = []                                # (kind, payload)
for grp in INV["clients"]:
    total = sum(n_items(i) for r in grp["rows"] for i in r["items"]) if grp["rows"][0]["label"] else 0
    cl_lines.append(("head", (grp["vlan"], total)))
    for r in grp["rows"]:
        line, first = "", True
        for it in r["items"]:
            cand = it if not line else line + " · " + it
            if line and measure(cand, 11.5, 600) > CL_TW:
                cl_lines.append(("row", (r["label"] if first else "", line, total)))
                line, first = it, False
            else:
                line = cand
        cl_lines.append(("row", (r["label"] if first else "", line, total)))
CLH = 64 + sum(26 if k == "head" else 17 for k, _ in cl_lines) + 8 * len(INV["clients"])
S.rect(CLX, CLY, CLW, CLH, rx=14, fill=PANEL, stroke=BORDER, sw=1.4)
S.text(CLX + 20, CLY + 32, "Clientes", 17, TEXT, 600)
S.text(CLX + 20, CLY + 52, "Wi-Fi y demás, del DHCP de UniFi", 12.5, MUTED, 400)
y = CLY + 64
for k, pl in cl_lines:
    if k == "head":
        vk, total = pl
        y += 8
        S.add(f'<circle cx="{CLX+24}" cy="{y+9}" r="4.5" fill="{C[vk]}"/>')
        S.text(CLX + 36, y + 13, vlan_tag(vk), 12, C[vk], 600, mono=True)
        if total:
            S.text(CLX + CLW - 16, y + 13, f"{total} clientes", 11.5, MUTED, 500, "end")
        y += 26
    else:
        lbl, txt, total_ = pl
        if lbl:
            S.text(CLX + 20, y + 4, lbl, 11.5, MUTED, 600)
        S.text(CLX + 20 + CL_LW, y + 4, txt, 11.5, TEXT if total_ else MUTED, 400)
        y += 17
CL_BOTTOM = CLY + CLH

# ================================================================ ZimaOS server
T = INV["server"]
tx0, ty0 = 40, max(DY2 + KVM[2] + 84, EY + EH + 84, CL_BOTTOM + 34 + 26)
tw0 = SYN_X - 20 - tx0
GY = 92
ZH = 440
th0 = GY + ZH + 22
device_card(tx0, ty0, tw0, th0, ["casaos"], T, C["1"], fill="#0f151e", stroke=C["1"] + "aa", sw=2)

S.line([(FP["2"], FB), (FP["2"], ty0)], C["1"], SPEED_W["1G"])
speed_tag(FP["2"] - 6, ty0 - 9, "1G", C["1"], anchor="end")
kx = KVM[0] + KVM[1] / 2
S.line([(kx, DY2 + KVM[2]), (kx, ty0)], MUTED, 2, dash="5 5", cap="butt")
S.label(kx + 10, (DY2 + KVM[2] + ty0) / 2 + 5, "HDMI + USB", MUTED, 12, anchor="start")

hwx, HWW = tx0 + 20, 250
hy0 = ty0 + GY
S.text(hwx + 2, hy0 + 12, "HARDWARE", 11.5, HWC, 700, ls=1.4)
TH_, TG_ = 58, 6


def hw_tile(hwi, x, y, w):
    S.rect(x, y, w, TH_, rx=11, fill="#151d29", stroke="#2e3a4c", sw=1.2)
    hw_bar(x, y, TH_)
    S.icon(hwi["icon"], x + 12, y + 15, 28)
    S.text(x + 50, y + 17, hwi["label"], 10, HWC, 700, ls=1.3)
    unk = "por confirmar" in hwi["value"]
    vs = min(15.5, 15.5 * (w - 62) / measure(hwi["value"], 15.5, 700))
    S.text(x + 50, y + 35, hwi["value"], round(vs, 2), MUTED if unk else TEXT, 700)
    if hwi.get("sub"):
        S.text(x + 50, y + 50, hwi["sub"], 10.5, MUTED, 400)


for i, hwi in enumerate(T["hw"]):
    hw_tile(hwi, hwx, hy0 + 22 + i * (TH_ + TG_), HWW)
assert hy0 + 22 + len(T["hw"]) * (TH_ + TG_) <= hy0 + ZH + 6, "hardware list too tall"

# ---- Docker zone
zx = hwx + HWW + 20
zy = hy0
zw = tx0 + tw0 - 20 - zx
S.rect(zx, zy, zw, ZH, rx=14, fill=C["1"] + "0a", stroke=C["1"] + "55", sw=1.3)
S.pill(zx + zw - 14, zy + 16, vlan_tag("1"), C["1"], 10.5, mono=True, h=19, pad=6, anchor="end")
S.text(zx + 18, zy + 31, f"{sum(len(a.get('rows', [1])) for g in T['groups'] for a in g['apps'])} contenedores Docker · "
       "un stack de Compose = una tarjeta", 12.5, MUTED, 400)

groups = {g["name"]: g for g in T["groups"]}
cgap = 14
NCOL = 4
cw = (zw - 32 - cgap * (NCOL - 1)) / NCOL
CH, CG = 56, 7
GT = HWC


def col_x(i):
    return zx + 16 + i * (cw + cgap)


def docker_badge(x, y):
    S.add(f'<circle cx="{x}" cy="{y}" r="12" fill="{PANEL}" stroke="#2f6fb0" stroke-width="1.2"/>')
    S.icon("docker", x - 8.5, y - 8.5, 17)


def chip_stack(g, x, y, w):
    """A Compose stack: header like a normal chip, then one row per container."""
    h = 46 + len(g["rows"]) * 21 + 8
    S.rect(x, y, w, h, rx=9, fill=CHIP, stroke=BORDER, sw=1)
    S.text(x + 16, y + 22, g["name"], 14.5, TEXT, 600)
    S.text(x + 16, y + 38, g.get("note", ""), 11.5, MUTED, 400)
    S.text(x + w - 12, y + 21, g["tag"], 11, FAINT, 500, "end", mono=True)
    for k, a in enumerate(g["rows"]):
        ry = y + 50 + k * 21
        S.icon(a["icon"], x + 16, ry, 17)
        S.text(x + 40, ry + 13, a["name"], 12.5, TEXT, 600, mono=False)
        S.text(x + 168, ry + 13, a.get("note", ""), 11.5, MUTED, 400)
    if g.get("docker"):
        docker_badge(x + 2, y + 2)
    return h


def chip(g, x, y, w):
    if g.get("rows"):
        return chip_stack(g, x, y, w)
    S.rect(x, y, w, CH, rx=9, fill=CHIP, stroke=BORDER, sw=1)
    S.icon(g["icons"][0], x + 12, y + (CH - 30) / 2, 30)
    nx = x + 54
    S.text(nx, y + 24, g["name"], 14.5, TEXT, 600)
    S.text(nx, y + 41, g.get("note", ""), 11.5, MUTED, 400)
    S.text(x + w - 12, y + 23, g.get("tag", ""), 11, FAINT, 500, "end", mono=True)
    return CH


def column(i, names):
    x, y = col_x(i), zy + 52
    for gname in names:
        S.text(x + 2, y + 12, gname.upper(), 11.5, GT, 700, ls=1.4)
        y += 22
        for g in groups[gname]["apps"]:
            y += chip(g, x, y, cw) + CG
        y += 14
    assert y <= zy + ZH + 6, f"Docker column {i} too tall: {y - zy}"


column(0, ["Red y gestión", "Casa"])
column(1, ["Fotos", "Documentos"])
column(2, ["Tesla"])
column(3, ["Lectura y notas"])

# ================================================================ Synology NAS (bottom right)
N_ = INV["nas"]
sx0, sw0 = SYN_X, W - 40 - SYN_X
device_card(sx0, ty0, sw0, th0, ["synology"], N_, C["1"])
S.line([(PORTS["9"], SB), (PORTS["9"], SB + 50), (SYN_IN, SB + 50), (SYN_IN, ty0)], C["1"], SPEED_W["1G"])
speed_tag(SYN_IN + 6, ty0 - 9, "1G", C["1"])
S.text(sx0 + 20, ty0 + GY + 12, "HARDWARE", 11.5, HWC, 700, ls=1.4)
nhw = len(N_["hw"])
hwn = (sw0 - 32 - (nhw - 1) * 10) / nhw
for i, hwi in enumerate(N_["hw"]):
    hw_tile(hwi, sx0 + 16 + i * (hwn + 10), ty0 + GY + 22, hwn)

DSY = ty0 + GY + 22 + TH_ + 30
S.text(sx0 + 20, DSY, "ALMACENAMIENTO", 11.5, HWC, 700, ls=1.4)

# drive bays of the NAS and its expansion unit, colored by the pool they belong to
BAY_W, BAY_P, BAY_H = 26, 31, 40
bay_pool = {d: pl for pl in N_["pools"] for d in pl["disks"]}
bx_ = sx0 + 20
bay_y = DSY + 30
for k, enc in enumerate(N_["enclosures"]):
    if enc.get("link"):
        S.line([(bx_ - 4, bay_y + BAY_H / 2), (bx_ + 52, bay_y + BAY_H / 2)], MUTED, 2.2, r=0)
        S.label(bx_ + 24, bay_y + BAY_H / 2 - 6, enc["link"], MUTED, 10.5, mono=True)
        bx_ += 60
    S.text(bx_, bay_y - 8, enc["name"], 12.5, TEXT, 600)
    for j in range(enc["bays"]):
        x = bx_ + j * BAY_P
        pl = bay_pool.get(f"{enc['name']}:{j+1}")
        if pl:
            S.rect(x, bay_y, BAY_W, BAY_H, rx=4, fill=DISK, stroke=pl["color"], sw=1.4)
            S.add(f'<circle cx="{x+BAY_W/2}" cy="{bay_y+15}" r="7.5" fill="none" stroke="{pl["color"]}" stroke-width="1.5"/>'
                  f'<circle cx="{x+BAY_W/2}" cy="{bay_y+15}" r="1.8" fill="{pl["color"]}"/>')
            S.rect(x + 7, bay_y + 31, 12, 3, rx=1, fill=DISK_EDGE, stroke="none", sw=0)
        else:                                   # empty bay
            S.rect(x, bay_y, BAY_W, BAY_H, rx=4, fill="none", stroke="#2e3a4c", sw=1.2, dash="3 3")
        S.text(x + BAY_W / 2, bay_y + BAY_H + 13, str(j + 1), 10, FAINT, 500, "middle", mono=True)
    bx_ += enc["bays"] * BAY_P + 8
S.rect(bx_ + 4, bay_y + 14, 12, 12, rx=2, fill="none", stroke="#2e3a4c", sw=1.2, dash="3 3")
S.text(bx_ + 22, bay_y + 24, "bahía vacía", 11, FAINT, 400)

# one row per pool / volume
py_ = bay_y + BAY_H + 28
PRH = 66
for pl in N_["pools"]:
    x, w = sx0 + 16, sw0 - 32
    S.rect(x, py_, w, PRH, rx=9, fill=CHIP, stroke=(pl["color"] + "88") if pl.get("warn") else BORDER, sw=1)
    S.add(f'<rect x="{x}" y="{py_+10}" width="4" height="{PRH-20}" rx="2" fill="{pl["color"]}"/>')
    S.text(x + 16, py_ + 22, pl["name"], 14.5, TEXT, 700)
    S.text(x + 16 + measure(pl["name"], 14.5) + 10, py_ + 22, pl["layout"], 12, MUTED, 400)
    if pl.get("warn"):
        S.pill(x + w - 12, py_ + 9, pl["warn"], "#f85149", 10, mono=True, h=18, pad=6, anchor="end")
    bwp = w - 32
    S.rect(x + 16, py_ + 31, bwp, 7, rx=3.5, fill="#253042", stroke="none", sw=0)
    barc = "#f85149" if pl["pct"] >= 90 else pl["color"]
    S.rect(x + 16, py_ + 31, max(bwp * pl["pct"] / 100, 7), 7, rx=3.5, fill=barc, stroke="none", sw=0)
    S.text(x + 16, py_ + 55, pl["sub"], 11.5, "#f85149" if pl.get("warn") else MUTED, 400)
    S.text(x + w - 12, py_ + 55, f"{pl['pct']:.0f} % · {pl['used']}", 11.5, TEXT, 500, "end")
    py_ += PRH + 8
S.text(sx0 + 20, py_ + 16, "SERVICIOS", 11.5, HWC, 700, ls=1.4)
nsv = len(N_["services"])
svw = (sw0 - 32 - (nsv - 1) * 9) / nsv
for i, sv in enumerate(N_["services"]):
    x, y = sx0 + 16 + i * (svw + 9), py_ + 26
    S.rect(x, y, svw, 50, rx=9, fill=CHIP, stroke=BORDER, sw=1)
    S.icon(sv["icon"], x + 9, y + 12, 26)
    S.text(x + 44, y + 22, sv["name"], 14, TEXT, 600)
    S.text(x + 44, y + 39, sv["note"], 11, MUTED, 400)
py_ += 26 + 50 + 8
assert py_ - 8 <= ty0 + th0 - 12, f"Synology storage too tall: {py_ - 8} > {ty0 + th0 - 12}"

H = ty0 + th0 + 40                      # canvas grows with the content

OUT.mkdir(exist_ok=True)
name = "homelab-public" if PUBLIC else ("homelab-unredacted" if UNREDACTED else "homelab")
svg_path = OUT / f"{name}.svg"
svg_path.write_text(S.render())
png_path = OUT / f"{name}.png"
subprocess.run(["rsvg-convert", "-z", "1.6", "-o", str(png_path), str(svg_path)], check=True)
print(svg_path, png_path)
if "--hq" in sys.argv:
    hq_path, pdf_path = OUT / f"{name}-hq.png", OUT / f"{name}.pdf"
    subprocess.run(["rsvg-convert", "-z", "4", "-o", str(hq_path), str(svg_path)], check=True)
    subprocess.run(["rsvg-convert", "-f", "pdf", "-o", str(pdf_path), str(svg_path)], check=True)
    print(hq_path, pdf_path)
