"""
ISA-101 Silver Theme constants and stylesheet.

Color palette matches the ISA-101 Themes white paper (August 2016)
using the default Silver Theme color set. Colors are organized by the
relative importance hierarchy defined in the white paper:
  1. Alarms (bright saturated — Red, Yellow, Purple)
  2. Abnormal status (saturated accent)
  3. Process values / information (readable, mid-contrast)
  4. Display information (subtle, cool, non-distracting)

Reference: white-paper-isa-operate-themes-en-57260.pdf
"""

# ══════════════════════════════════════════════════════════════════════
# ISA-101 Silver Theme — Base Colors
# ══════════════════════════════════════════════════════════════════════
BG_MAIN = "#E0E2EB"          # Picture background (224, 226, 235)
BG_PANEL = "#EBECF1"         # Dynamo background (235, 236, 241)
BG_INSET = "#DBDBE0"         # PV AI/ALM background (219, 219, 224)
BG_RAISED = "#EBECF1"        # Raised elements = dynamo bg
BG_DARK = "#7B92AD"          # Dark headers — Alarm 2 dark grey-blue (123, 146, 173)
BG_FACEPLATE = "#EBECF1"     # Dynamo background
BG_TREND = "#192F3D"         # Trend background (equipment outline dark, 25, 47, 61)
BG_TOOLBAR = "#D0D2DB"       # Toolbar (slightly darker than main)

# ══════════════════════════════════════════════════════════════════════
# Text Colors
# ══════════════════════════════════════════════════════════════════════
TEXT_PRIMARY = "#000000"      # SP, PV, Number values on background — black
TEXT_SECONDARY = "#0E3260"    # Informational text — Tag names, EU (14, 50, 96)
TEXT_DISABLED = "#9BADC6"     # Disabled / subtle — Alarm 1 grey-blue (155, 173, 198)
TEXT_HEADER = "#0E3260"       # Same dark blue as informational text
TEXT_ON_DARK = "#DEE7EF"      # Light text on dark backgrounds (222, 231, 239)

# ══════════════════════════════════════════════════════════════════════
# Dynamo Colors (from Theme_Colors Color Table)
# ══════════════════════════════════════════════════════════════════════
DV_PV_BG = "#DFD7CC"          # PV loop background, light tan (223, 215, 204)
DV_SP_WORK = "#CDC2B6"        # SP Work color, dark tan (205, 194, 182)
DV_PV_FG = "#3C6291"          # PV foreground dark blue (60, 98, 145)
DV_PV_LEVEL_BG = "#CBD9E2"    # PV level background, light blue (203, 217, 226)
DV_OUT_FG = "#14696A"         # OUT bargraph foreground blue-green (20, 105, 106)
DV_OUT_BG = "#83B4AC"         # OUT bargraph background, light green (131, 180, 172)
DV_ALARM_BAR1 = "#9BADC6"     # Alarm 1 grey-blue (155, 173, 198)
DV_ALARM_BAR2 = "#7B92AD"     # Alarm 2 dark grey-blue (123, 146, 173)
DV_STATUS_BORDER = "#0000C6"  # Status border outline blue (0, 0, 198)
DV_MODULE_SELECT = "#9D4F00"  # Module select pumpkin (157, 79, 0)

# ══════════════════════════════════════════════════════════════════════
# Pipe and Tank Colors
# ══════════════════════════════════════════════════════════════════════
DV_PIPE = "#6B8DAF"            # Main pipe color (107, 141, 175)
DV_EQUIP = "#B2C7D5"          # Main big equipment (178, 199, 213)
DV_EQUIP_OUTLINE = "#192F3D"  # Big equipment outline (25, 47, 61)
DV_EQUIP_DARK1 = "#8BABC0"    # Equipment Dark 1 / Highlight (139, 171, 192)
DV_EQUIP_DARK2 = "#7797AC"    # Equipment Dark 2 (119, 151, 172)
DV_EQUIP_DARK3 = "#598196"    # Equipment Dark 3 (89, 129, 150)
DV_TEXT_ON_TANK = "#243C59"    # Text on tanks (36, 60, 89)

# ══════════════════════════════════════════════════════════════════════
# Equipment ON/OFF Colors (same across all themes)
# ══════════════════════════════════════════════════════════════════════
DV_EQUIP_OUTLINE_DYN = "#75849B"  # Equipment (pump/valve) outline (117, 132, 155)
DV_EQUIP_ON = "#EEF2F9"           # Discrete/Analog ON — off-white (238, 242, 249)
DV_EQUIP_OFF = "#B4B9C8"          # Discrete/Analog OFF — silver-grey (180, 185, 200)

# ══════════════════════════════════════════════════════════════════════
# Process Variable Colors (ISA-compatible, less saturated)
# ══════════════════════════════════════════════════════════════════════
COLOR_PV = "#3C6291"          # PV foreground dark blue (60, 98, 145)
COLOR_SP = "#CDC2B6"          # SP Work color (dark tan)
COLOR_SP_LIGHT = "#CDC2B6"    # SP on light bg (same)
COLOR_OP = "#14696A"          # OUT foreground blue-green (20, 105, 106)
COLOR_LEVEL = "#5B8BA0"       # Blue — drum level (muted)
COLOR_STEAM = "#6B8DAF"       # Pipe color — steam (107, 141, 175)
COLOR_GAS = "#9D6B33"         # Warm brown — fuel gas (muted)
COLOR_AIR = "#5B8BA0"         # Blue-grey — combustion air (muted)
COLOR_O2 = "#4A8A5A"          # Muted green — stack O2
COLOR_PRESS = "#6B5B8A"       # Muted purple — pressure
COLOR_FWT = "#8A7B4A"         # Muted tan — FW temp
COLOR_FW = "#3A8A7A"          # Muted teal — feedwater

# ══════════════════════════════════════════════════════════════════════
# Alarm Colors — ISA standard (same across all themes)
# Per white paper: "alarm colors should only be used for alarms"
# ══════════════════════════════════════════════════════════════════════
ALARM_CRITICAL = "#FF0000"    # Red — critical / HH / LL (255, 0, 0)
ALARM_HIGH = "#FF0000"        # Red — same as critical in ISA
ALARM_WARN = "#FFFF00"        # Yellow — warning / HI / LO (255, 255, 0)
ALARM_ADVISORY = "#6F3198"    # Purple — advisory (111, 49, 152)
ALARM_INFO = "#6F3198"        # Purple — same as advisory
ALARM_OK = "#9BADC6"          # Grey-blue — normal (no saturated green)

# ══════════════════════════════════════════════════════════════════════
# Control Status Colors (ISA)
# ══════════════════════════════════════════════════════════════════════
STATUS_AUTO = "#9BADC6"       # Grey-blue — normal (AUTO = expected, not highlighted)
STATUS_MAN = "#9D4F00"        # Pumpkin — manual (abnormal, draws attention)
STATUS_CAS = "#0000C6"        # Blue — cascade
STATUS_OOS = "#FF0000"        # Red — out of service (alarm-level attention)
STATUS_INIT = "#6F3198"       # Purple — initializing

# ══════════════════════════════════════════════════════════════════════
# Trend Colors — ISA compatible palette
# ══════════════════════════════════════════════════════════════════════
TREND_COLORS = [
    "#3C6291",   # 1 - dark blue (PV color)
    "#CDC2B6",   # 2 - tan (SP color)
    "#14696A",   # 3 - blue-green (OUT color)
    "#6B8DAF",   # 4 - pipe blue
    "#9D4F00",   # 5 - pumpkin
    "#6F3198",   # 6 - purple
    "#8BABC0",   # 7 - light blue
    "#83B4AC",   # 8 - light green
]

# ══════════════════════════════════════════════════════════════════════
# Arrows / Links / Other Colors
# ══════════════════════════════════════════════════════════════════════
DV_ARROW1 = "#6B8EAD"          # Arrow 1 grey-blue (107, 142, 173)
DV_ARROW2 = "#6BC36B"          # Arrow 2 green (107, 195, 107)
DV_TITLE_BG = "#CBD9E2"        # Display title (203, 217, 226)
DV_TITLE_OUTLINE = "#B2C7D5"   # Title outline (178, 199, 213)
DV_CTRL_LINE = "#C6C6C6"       # Control information lines (198, 198, 198)

# (Stylesheet in stylesheet.py)
