#!/usr/bin/env python3
"""
SADE – System for Autonomous Decision Evaluation
Agent Architecture Visualizer  ·  PyQt5 GUI

Usage:
    python gui.py                          # opens with empty state
    python gui.py results/path/file.txt    # opens and loads a result file
"""

import sys
import json
import math
from pathlib import Path
from typing import Optional, Dict, Any, List, Tuple

from PyQt5.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QTextEdit, QScrollArea, QSplitter, QFrame,
    QFileDialog, QTabWidget, QComboBox, QGroupBox, QGridLayout,
    QSizePolicy, QStatusBar, QSpacerItem,
)
from PyQt5.QtCore import Qt, QRect, QPoint, QSize, QRectF, QPointF, pyqtSignal
from PyQt5.QtGui import (
    QPainter, QPen, QBrush, QColor, QFont, QFontMetrics,
    QPainterPath, QLinearGradient, QPolygonF, QPalette,
)

# ─── Palette ──────────────────────────────────────────────────────────────────
BG          = "#0D1117"
PANEL_BG    = "#161B22"
CARD_BG     = "#21262D"
BORDER      = "#30363D"
TXT_HI      = "#E6EDF3"
TXT_MED     = "#8B949E"
TXT_LO      = "#484F58"
ACCENT      = "#58A6FF"

C_ORCHSTR   = "#7C4DFF"
C_ENTRY     = "#29B6F6"
C_ENV       = "#26A69A"
C_REP       = "#FF7043"
C_CLAIMS    = "#42A5F5"
C_DECISION  = "#9E9E9E"

C_APPROVED  = "#3FB950"
C_APPROVEDC = "#E3B341"
C_ACTION    = "#58A6FF"
C_DENIED    = "#F85149"
C_UNKNOWN   = "#6E7681"

# ─── Helpers ──────────────────────────────────────────────────────────────────

def _qc(hex_color: str) -> QColor:
    return QColor(hex_color)


def decision_color(dtype: str) -> str:
    return {
        "APPROVED":             C_APPROVED,
        "APPROVED-CONSTRAINTS": C_APPROVEDC,
        "ACTION-REQUIRED":      C_ACTION,
        "DENIED":               C_DENIED,
    }.get(dtype, C_UNKNOWN)


def risk_color(level: str) -> str:
    return {"LOW": C_APPROVED, "MEDIUM": C_APPROVEDC, "HIGH": C_DENIED}.get(
        level.upper() if level else "", C_UNKNOWN
    )


def _badge_html(text: str, color: str) -> str:
    return (
        f'<span style="background:{color};color:#fff;'
        f'padding:1px 8px;border-radius:4px;font-size:11px;">'
        f"{text}</span>"
    )


def _section_html(title: str, rows: List[Tuple[str, str]], badge_map: Dict[str, str] = None) -> str:
    badge_map = badge_map or {}
    html = (
        f'<div style="margin-bottom:22px;">'
        f'<div style="font-size:11px;font-weight:700;color:{ACCENT};'
        f'text-transform:uppercase;letter-spacing:1.2px;margin-bottom:10px;">'
        f"{title}</div>"
        f'<table cellspacing="0" cellpadding="0" width="100%">'
    )
    for key, val in rows:
        if key in badge_map:
            val_html = _badge_html(val, badge_map[key])
        else:
            val_html = f'<span style="color:{TXT_HI};">{val}</span>'
        html += (
            f'<tr>'
            f'<td style="color:{TXT_MED};width:200px;padding:4px 20px 4px 0;'
            f'vertical-align:top;white-space:nowrap;">{key}</td>'
            f'<td style="padding:4px 0;vertical-align:top;">{val_html}</td>'
            f'</tr>'
        )
    html += "</table></div>"
    return html


def _list_html(title: str, items: List[str], color: str = TXT_HI) -> str:
    if not items:
        return _section_html(title, [("(none)", "")])
    html = (
        f'<div style="margin-bottom:22px;">'
        f'<div style="font-size:11px;font-weight:700;color:{ACCENT};'
        f'text-transform:uppercase;letter-spacing:1.2px;margin-bottom:10px;">'
        f"{title}</div>"
    )
    for item in items:
        html += f'<div style="color:{color};margin-bottom:6px;line-height:1.5;">• {item}</div>'
    html += "</div>"
    return html


def _prose_html(title: str, text: str) -> str:
    if not text:
        return ""
    return (
        f'<div style="margin-bottom:22px;">'
        f'<div style="font-size:11px;font-weight:700;color:{ACCENT};'
        f'text-transform:uppercase;letter-spacing:1.2px;margin-bottom:10px;">'
        f"{title}</div>"
        f'<div style="color:{TXT_HI};line-height:1.7;">{text}</div>'
        f"</div>"
    )


def _hr() -> str:
    return f'<hr style="border:none;border-top:1px solid {BORDER};margin:18px 0;">'


def _wrap(inner: str) -> str:
    return (
        f'<html><body style="background:{CARD_BG};color:{TXT_HI};'
        f'font-family:\'Segoe UI\',system-ui,sans-serif;font-size:13px;'
        f'margin:22px 24px;line-height:1.6;">'
        f"{inner}"
        f"</body></html>"
    )


# ─── Architecture Diagram ─────────────────────────────────────────────────────

class _Node:
    """Data for a single diagram node."""
    def __init__(self, nid: str, label: str, sub: str, rx: float, ry: float,
                 w: float, h: float, color: str, tab_idx: int):
        self.nid     = nid
        self.label   = label
        self.sub     = sub
        self.rx      = rx      # relative centre-x  (0-1)
        self.ry      = ry      # relative centre-y  (0-1)
        self.rw      = w       # relative half-width
        self.rh      = h       # relative half-height
        self.color   = color
        self.tab_idx = tab_idx
        self.active  = False   # lit up when data is loaded
        self.status  = ""      # e.g. decision type label

    def rect(self, W: int, H: int) -> QRectF:
        cx, cy = self.rx * W, self.ry * H
        hw, hh = self.rw * W, self.rh * H
        return QRectF(cx - hw, cy - hh, hw * 2, hh * 2)

    def centre(self, W: int, H: int) -> QPointF:
        return QPointF(self.rx * W, self.ry * H)


NODES_DEF = [
    _Node("entry",    "Entry Request",     "",              0.50, 0.07, 0.26, 0.055, C_ENTRY,    0),
    _Node("orch",     "Orchestrator",      "Decision Authority", 0.50, 0.26, 0.30, 0.072, C_ORCHSTR,  -1),
    _Node("env",      "Environment",       "Agent",         0.17, 0.58, 0.22, 0.060, C_ENV,      1),
    _Node("rep",      "Reputation",        "Agent",         0.50, 0.58, 0.22, 0.060, C_REP,      2),
    _Node("claims",   "Claims",            "Agent",         0.83, 0.58, 0.22, 0.060, C_CLAIMS,   3),
    _Node("decision", "Final Decision",    "",              0.50, 0.88, 0.30, 0.065, C_DECISION, 4),
]

# edges: (from_id, to_id, bidirectional, dashed)
EDGES = [
    ("entry",  "orch",     False, False),
    ("orch",   "env",      True,  False),
    ("orch",   "rep",      True,  False),
    ("orch",   "claims",   True,  True),   # dashed = optional
    ("orch",   "decision", False, False),
]


class ArchitectureDiagram(QWidget):
    node_clicked = pyqtSignal(int)   # emits tab_idx

    def __init__(self, parent=None):
        super().__init__(parent)
        self.nodes: Dict[str, _Node] = {n.nid: n for n in NODES_DEF}
        self._hovered: Optional[str] = None
        self.setMouseTracking(True)
        self.setMinimumSize(300, 480)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    # ── public API ────────────────────────────────────────────────────────────

    def load_result(self, data: Dict[str, Any]):
        """Activate nodes and set their status labels from result data."""
        decision_type = data.get("decision", {}).get("type", "")
        for nid, node in self.nodes.items():
            node.active = True
            node.status = ""
        self.nodes["decision"].color  = decision_color(decision_type)
        self.nodes["decision"].status = decision_type
        vis = data.get("visibility", {})
        claims_called = vis.get("claims_agent", {}).get("called", False)
        self.nodes["claims"].active = bool(claims_called)
        self.update()

    def clear(self):
        for node in self.nodes.values():
            node.active = False
            node.status = ""
            node.color = {
                "entry": C_ENTRY, "orch": C_ORCHSTR, "env": C_ENV,
                "rep": C_REP, "claims": C_CLAIMS, "decision": C_DECISION,
            }.get(node.nid, C_UNKNOWN)
        self.update()

    # ── events ────────────────────────────────────────────────────────────────

    def mouseMoveEvent(self, event):
        W, H = self.width(), self.height()
        p = QPointF(event.pos())
        self._hovered = None
        for nid, node in self.nodes.items():
            if node.rect(W, H).contains(p):
                self._hovered = nid
                self.setCursor(Qt.PointingHandCursor)
                break
        else:
            self.setCursor(Qt.ArrowCursor)
        self.update()

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        W, H = self.width(), self.height()
        p = QPointF(event.pos())
        for nid, node in self.nodes.items():
            if node.rect(W, H).contains(p) and node.tab_idx >= 0:
                self.node_clicked.emit(node.tab_idx)
                return

    def leaveEvent(self, event):
        self._hovered = None
        self.setCursor(Qt.ArrowCursor)
        self.update()

    # ── painting ──────────────────────────────────────────────────────────────

    def paintEvent(self, event):
        W, H = self.width(), self.height()
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.setRenderHint(QPainter.TextAntialiasing)

        # background
        p.fillRect(0, 0, W, H, _qc(PANEL_BG))

        self._draw_edges(p, W, H)
        self._draw_nodes(p, W, H)

    def _arrow_head(self, painter: QPainter, tip: QPointF, angle_deg: float,
                    size: float, color: QColor):
        """Draw a filled arrowhead triangle at `tip` pointing in `angle_deg`."""
        a = math.radians(angle_deg)
        wing = math.radians(25)
        p1 = QPointF(tip.x() - size * math.cos(a - wing),
                     tip.y() - size * math.sin(a - wing))
        p2 = QPointF(tip.x() - size * math.cos(a + wing),
                     tip.y() - size * math.sin(a + wing))
        poly = QPolygonF([tip, p1, p2])
        painter.setBrush(QBrush(color))
        painter.setPen(Qt.NoPen)
        painter.drawPolygon(poly)

    def _edge_colour(self, fid: str, tid: str) -> QColor:
        fn = self.nodes[fid]
        tn = self.nodes[tid]
        if fn.active and tn.active:
            return _qc("#58A6FF")
        return _qc(TXT_LO)

    def _draw_edges(self, p: QPainter, W: int, H: int):
        for (fid, tid, bidir, dashed) in EDGES:
            fn = self.nodes[fid]
            tn = self.nodes[tid]
            color = self._edge_colour(fid, tid)
            pen = QPen(color, 1.8)
            if dashed:
                pen.setStyle(Qt.DashLine)
                pen.setDashPattern([4, 4])
            p.setPen(pen)

            fc = fn.centre(W, H)
            tc = tn.centre(W, H)
            # clip start/end to node border
            start = self._clip_to_rect(fc, tc, fn.rect(W, H))
            end   = self._clip_to_rect(tc, fc, tn.rect(W, H))

            p.drawLine(start, end)

            # arrowhead at end
            dx, dy = end.x() - start.x(), end.y() - start.y()
            angle  = math.degrees(math.atan2(dy, dx))
            self._arrow_head(p, end, angle, 8, color)

            if bidir:
                self._arrow_head(p, start, angle + 180, 8, color)

    @staticmethod
    def _clip_to_rect(src: QPointF, dst: QPointF, rect: QRectF) -> QPointF:
        """Return the point where the line from src→dst exits `rect`."""
        cx, cy = rect.center().x(), rect.center().y()
        hw, hh = rect.width() / 2, rect.height() / 2
        dx, dy = dst.x() - src.x(), dst.y() - src.y()
        if dx == 0 and dy == 0:
            return src
        # parametric intersection with box edges
        t_candidates = []
        for sign_x in [-1, 1]:
            if dx != 0:
                t = (cx + sign_x * hw - src.x()) / dx
                y = src.y() + t * dy
                if cy - hh <= y <= cy + hh and t > 0:
                    t_candidates.append(t)
        for sign_y in [-1, 1]:
            if dy != 0:
                t = (cy + sign_y * hh - src.y()) / dy
                x = src.x() + t * dx
                if cx - hw <= x <= cx + hw and t > 0:
                    t_candidates.append(t)
        if not t_candidates:
            return src
        t_min = min(t_candidates)
        return QPointF(src.x() + t_min * dx, src.y() + t_min * dy)

    def _draw_nodes(self, p: QPainter, W: int, H: int):
        for nid, node in self.nodes.items():
            rect   = node.rect(W, H)
            color  = _qc(node.color)
            hov    = (nid == self._hovered)
            active = node.active

            # shadow
            shadow_rect = QRectF(rect.x() + 3, rect.y() + 3, rect.width(), rect.height())
            p.setPen(Qt.NoPen)
            p.setBrush(QColor(0, 0, 0, 60))
            p.drawRoundedRect(shadow_rect, 10, 10)

            # gradient fill
            grad = QLinearGradient(rect.topLeft(), rect.bottomLeft())
            base = QColor(color)
            if active:
                base.setAlpha(255)
                grad.setColorAt(0.0, base.lighter(130))
                grad.setColorAt(1.0, base)
            else:
                grad.setColorAt(0.0, _qc(CARD_BG))
                grad.setColorAt(1.0, _qc(CARD_BG))

            p.setBrush(QBrush(grad))

            # border
            border_color = color if active else _qc(BORDER)
            if hov and node.tab_idx >= 0:
                border_color = color.lighter(160) if active else _qc(ACCENT)
            pen_w = 2.0 if (active or hov) else 1.0
            p.setPen(QPen(border_color, pen_w))
            p.drawRoundedRect(rect, 10, 10)

            # text
            cx = rect.center().x()
            cy = rect.center().y()

            if node.status:
                # show status (decision type) on the node
                f = QFont("Segoe UI", 8, QFont.Bold)
                p.setFont(f)
                p.setPen(_qc("#FFFFFF"))
                p.drawText(rect.adjusted(4, 0, -4, -4),
                            Qt.AlignCenter | Qt.AlignBottom,
                            node.status)
                label_y = cy - 6
            else:
                label_y = cy

            # main label
            f = QFont("Segoe UI", 9, QFont.Bold)
            p.setFont(f)
            txt_color = _qc(TXT_HI) if active else _qc(TXT_MED)
            p.setPen(txt_color)
            p.drawText(rect.adjusted(4, 0, -4, -4),
                        Qt.AlignHCenter | Qt.AlignVCenter,
                        node.label)

            # sub-label
            if node.sub:
                f2 = QFont("Segoe UI", 7)
                p.setFont(f2)
                p.setPen(_qc(TXT_MED) if active else _qc(TXT_LO))
                sub_rect = QRectF(rect.x(), rect.center().y() + 2,
                                  rect.width(), rect.height() / 2 - 2)
                p.drawText(sub_rect, Qt.AlignHCenter | Qt.AlignTop, node.sub)


# ─── Styled Text Display ──────────────────────────────────────────────────────

class HtmlView(QTextEdit):
    def __init__(self):
        super().__init__()
        self.setReadOnly(True)
        self.setStyleSheet(f"""
            QTextEdit {{
                background: {CARD_BG};
                color: {TXT_HI};
                border: 1px solid {BORDER};
                border-radius: 6px;
                padding: 12px;
                font-family: 'Segoe UI', system-ui, sans-serif;
                font-size: 13px;
            }}
        """)

    def set_html(self, html: str):
        self.setHtml(html)
        self.moveCursor(self.textCursor().Start)


# ─── Tab Content Builders ─────────────────────────────────────────────────────

def build_entry_tab(data: Dict[str, Any]) -> str:
    vis = data.get("visibility", {})
    er  = vis.get("entry_request", {})
    rows = [
        ("Zone ID",       er.get("sade_zone_id", "—")),
        ("Pilot ID",      er.get("pilot_id", "—")),
        ("Organization",  er.get("organization_id", "—")),
        ("Drone ID",      er.get("drone_id", "—")),
        ("Payload (kg)",  str(er.get("payload", "—"))),
        ("Entry Time",    er.get("requested_entry_time", "—")),
        ("Request Type",  er.get("request_type", "—")),
    ]
    return _wrap(_section_html("Entry Request", rows))


def build_env_tab(data: Dict[str, Any]) -> str:
    env = data.get("visibility", {}).get("environment_agent", {})
    if not env:
        return _wrap("<i>No environment agent data.</i>")

    mfc   = env.get("manufacturer_fc", {})
    raw   = env.get("raw_conditions", {})
    risk  = env.get("risk_assessment", {})
    sc    = raw.get("spatial_constraints", {})

    mfc_rows = [
        ("Manufacturer",  mfc.get("manufacturer", "—")),
        ("Model",         mfc.get("model", "—")),
        ("Category",      mfc.get("category", "—")),
        ("Max Payload",   f"{mfc.get('mfc_payload_max_kg', '—')} kg"),
        ("Max Wind",      f"{mfc.get('mfc_max_wind_kt', '—')} kt"),
    ]
    cond_rows = [
        ("Wind Speed",    f"{raw.get('wind', '—')} kt"),
        ("Wind Gust",     f"{raw.get('wind_gust', '—')} kt"),
        ("Precipitation", raw.get("precipitation", "—")),
        ("Visibility",    f"{raw.get('visibility', '—')} km"),
        ("Light",         raw.get("light_conditions", "—")),
        ("Airspace Class",sc.get("airspace_class", "—")),
    ]
    rl = risk.get("risk_level", "")
    risk_rows = [
        ("Overall Risk",    rl),
        ("Wind Risk",       env.get("recommendation_wind", "—")),
        ("Payload Risk",    env.get("recommendation_payload", "—")),
    ]
    badge_map = {
        "Overall Risk":  risk_color(rl),
        "Wind Risk":     risk_color(env.get("recommendation_wind", "")),
        "Payload Risk":  risk_color(env.get("recommendation_payload", "")),
    }

    html = (
        _section_html("Manufacturer Flight Constraints", mfc_rows)
        + _hr()
        + _section_html("Environmental Conditions", cond_rows)
        + _hr()
        + _section_html("Risk Assessment", risk_rows, badge_map=badge_map)
        + _hr()
        + _list_html("Blocking Factors", risk.get("blocking_factors", []), C_DENIED)
        + _list_html("Marginal Factors",  risk.get("marginal_factors", []), C_APPROVEDC)
        + _list_html("Wind Constraints Suggested", env.get("constraint_suggestions_wind", []))
        + _list_html("Payload Constraints Suggested", env.get("constraint_suggestions_payload", []))
        + _prose_html("Wind Recommendation", env.get("recommendation_prose_wind", ""))
        + _prose_html("Payload Recommendation", env.get("recommendation_prose_payload", ""))
        + _prose_html("Wind Analysis", env.get("why_prose_wind", ""))
        + _prose_html("Payload Analysis", env.get("why_prose_payload", ""))
    )
    return _wrap(html)


def build_rep_tab(data: Dict[str, Any]) -> str:
    rep = data.get("visibility", {}).get("reputation_agent", {})
    if not rep:
        return _wrap("<i>No reputation agent data.</i>")

    ia   = rep.get("incident_analysis", {})
    risk = rep.get("risk_assessment", {})
    rl   = risk.get("risk_level", "")
    incidents: List[Dict] = ia.get("incidents", [])

    summary_rows = [
        ("Total Incidents",    str(ia.get("total_incidents", 0))),
        ("Recent Incidents",   str(ia.get("recent_incidents_count", 0))),
        ("Unresolved Present", "Yes" if ia.get("unresolved_incidents_present") else "No"),
        ("DRP Sessions",       str(rep.get("drp_sessions_count", 0))),
        ("Demo Steady Max",    f"{rep.get('demo_steady_max_kt', 0)} kt"),
        ("Demo Gust Max",      f"{rep.get('demo_gust_max_kt', 0)} kt"),
        ("n_0100_0101",        str(rep.get("n_0100_0101", 0))),
    ]
    risk_rows = [
        ("Risk Level",         rl),
        ("Recommendation",     rep.get("recommendation", "—")),
    ]
    badge_map = {
        "Risk Level":     risk_color(rl),
        "Recommendation": risk_color(rep.get("recommendation", "")),
    }

    html = (
        _section_html("Reputation Summary", summary_rows)
        + _hr()
        + _section_html("Risk Assessment", risk_rows, badge_map=badge_map)
        + _hr()
        + _list_html("Blocking Factors", risk.get("blocking_factors", []), C_DENIED)
        + _list_html("Confidence Factors", risk.get("confidence_factors", []), C_APPROVED)
        + _hr()
    )

    # Incidents table
    if incidents:
        html += (
            f'<div style="font-size:11px;font-weight:700;color:{ACCENT};'
            f'text-transform:uppercase;letter-spacing:1.2px;margin-bottom:12px;">Incidents</div>'
        )
        for inc in incidents:
            sev   = inc.get("severity", "")
            res   = inc.get("resolved", False)
            sc    = risk_color(sev)
            rc    = C_APPROVED if res else C_DENIED
            html += (
                f'<div style="background:{PANEL_BG};border:1px solid {BORDER};'
                f'border-radius:6px;padding:12px 14px;margin-bottom:10px;">'
                f'<table cellspacing="0" cellpadding="0" width="100%"'
                f' style="margin-bottom:6px;"><tr>'
                f'<td style="color:{TXT_HI};font-weight:600;vertical-align:middle;">'
                f'{inc.get("incident_code","")}</td>'
                f'<td style="text-align:right;vertical-align:middle;white-space:nowrap;">'
                f'{_badge_html(sev, sc)}&nbsp;{_badge_html("RESOLVED" if res else "UNRESOLVED", rc)}'
                f'</td></tr></table>'
                f'<div style="color:{TXT_MED};font-size:12px;margin-bottom:4px;">'
                f'{inc.get("incident_category","")} › {inc.get("incident_subcategory","")}</div>'
                f'<div style="color:{TXT_LO};font-size:11px;">'
                f'{inc.get("date","")}</div>'
                f'</div>'
            )
        html += _hr()

    html += (
        _prose_html("Recommendation", rep.get("recommendation_prose", ""))
        + _prose_html("Why", rep.get("why_prose", ""))
    )
    return _wrap(html)


def build_claims_tab(data: Dict[str, Any]) -> str:
    ca = data.get("visibility", {}).get("claims_agent", {})
    if not ca:
        return _wrap("<i>No claims agent data.</i>")

    called    = ca.get("called", False)
    satisfied = ca.get("satisfied", False)

    if not called:
        return _wrap(
            f'<div style="text-align:center;margin-top:40px;">'
            f'<div style="font-size:28px;color:{TXT_LO};">—</div>'
            f'<div style="font-size:14px;color:{TXT_MED};margin-top:8px;">'
            f'Claims Agent was not called for this request.</div>'
            f'</div>'
        )

    rows = [
        ("Called",    "Yes"),
        ("Satisfied", "Yes" if satisfied else "No"),
    ]
    badge_map = {
        "Called":    ACCENT,
        "Satisfied": C_APPROVED if satisfied else C_DENIED,
    }

    html = (
        _section_html("Status", rows, badge_map=badge_map)
        + _hr()
        + _list_html("Resolved Incident Prefixes",   ca.get("resolved_incident_prefixes", []),   C_APPROVED)
        + _list_html("Unresolved Incident Prefixes", ca.get("unresolved_incident_prefixes", []), C_DENIED)
        + _list_html("Satisfied Actions",   ca.get("satisfied_actions", []),   C_APPROVED)
        + _list_html("Unsatisfied Actions", ca.get("unsatisfied_actions", []), C_DENIED)
        + _prose_html("Recommendation", ca.get("recommendation_prose", ""))
        + _prose_html("Why", ca.get("why_prose", ""))
    )
    return _wrap(html)


def build_decision_tab(data: Dict[str, Any]) -> str:
    dec  = data.get("decision", {})
    dtype = dec.get("type", "UNKNOWN")
    dc    = decision_color(dtype)
    vis   = data.get("visibility", {})
    trace = vis.get("rule_trace", [])

    header = (
        f'<div style="text-align:center;padding:20px 0 16px;">'
        f'<div style="font-size:32px;font-weight:900;color:{dc};'
        f'letter-spacing:2px;">{dtype}</div>'
        f'</div>'
    )

    msg_html = (
        f'<div style="background:{PANEL_BG};border-left:4px solid {dc};'
        f'border-radius:4px;padding:10px 14px;margin-bottom:14px;'
        f'font-size:13px;font-weight:600;color:{TXT_HI};">'
        f'{dec.get("sade_message","")}'
        f'</div>'
    )

    html = header + msg_html

    if dec.get("constraints"):
        html += _list_html("Constraints", dec["constraints"], C_APPROVEDC)
    if dec.get("actions"):
        html += _list_html("Actions Required", dec["actions"], C_ACTION)
    if dec.get("denial_code"):
        html += _section_html("Denial", [("Code", dec["denial_code"])],
                              badge_map={"Code": C_DENIED})
    html += _prose_html("Explanation", dec.get("explanation", ""))
    if trace:
        html += _hr() + _list_html("Rule Trace", trace, ACCENT)

    return _wrap(html)


# ─── Tab Widget ───────────────────────────────────────────────────────────────

TAB_LABELS = [
    ("Entry Request", C_ENTRY),
    ("Environment",   C_ENV),
    ("Reputation",    C_REP),
    ("Claims",        C_CLAIMS),
    ("Decision",      C_ORCHSTR),
]

TAB_BUILDERS = [
    build_entry_tab,
    build_env_tab,
    build_rep_tab,
    build_claims_tab,
    build_decision_tab,
]


class DetailTabs(QTabWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._views: List[HtmlView] = []
        for label, color in TAB_LABELS:
            view = HtmlView()
            view.set_html(_wrap(
                f'<div style="text-align:center;margin-top:60px;color:{TXT_MED};">'
                f'Load a result file to view data.</div>'
            ))
            sa = QScrollArea()
            sa.setWidget(view)
            sa.setWidgetResizable(True)
            sa.setStyleSheet(f"QScrollArea{{border:none;background:{CARD_BG};}}")
            self.addTab(sa, label)
            self._views.append(view)

        self.setStyleSheet(f"""
            QTabWidget::pane {{
                border: 1px solid {BORDER};
                border-radius: 6px;
                background: {CARD_BG};
            }}
            QTabBar::tab {{
                background: {PANEL_BG};
                color: {TXT_MED};
                padding: 10px 22px;
                border: 1px solid {BORDER};
                border-bottom: none;
                border-radius: 5px 5px 0 0;
                font-size: 12px;
                margin-right: 2px;
            }}
            QTabBar::tab:selected {{
                background: {CARD_BG};
                color: {TXT_HI};
                font-weight: 600;
            }}
            QTabBar::tab:hover {{
                background: {CARD_BG};
                color: {TXT_HI};
            }}
        """)

    def load_result(self, data: Dict[str, Any]):
        for i, builder in enumerate(TAB_BUILDERS):
            html = builder(data)
            self._views[i].set_html(html)

    def clear(self):
        placeholder = _wrap(
            f'<div style="text-align:center;margin-top:60px;color:{TXT_MED};">'
            f'Load a result file to view data.</div>'
        )
        for v in self._views:
            v.set_html(placeholder)


# ─── Toolbar / Header ─────────────────────────────────────────────────────────

_BTN_STYLE = f"""
    QPushButton {{
        background: {CARD_BG};
        color: {TXT_HI};
        border: 1px solid {BORDER};
        border-radius: 5px;
        padding: 6px 16px;
        font-size: 12px;
    }}
    QPushButton:hover {{ background: #2D333B; border-color: {ACCENT}; }}
    QPushButton:pressed {{ background: #1C2128; }}
"""

_COMBO_STYLE = f"""
    QComboBox {{
        background: {CARD_BG};
        color: {TXT_HI};
        border: 1px solid {BORDER};
        border-radius: 5px;
        padding: 5px 10px;
        font-size: 12px;
        min-width: 240px;
    }}
    QComboBox::drop-down {{ border: none; width: 24px; }}
    QComboBox QAbstractItemView {{
        background: {CARD_BG};
        color: {TXT_HI};
        border: 1px solid {BORDER};
        selection-background-color: #2D333B;
    }}
"""


# ─── Main Window ──────────────────────────────────────────────────────────────

class SADEWindow(QMainWindow):

    # preset result files (relative to project root)
    PRESETS = {
        "Weather · Wind Good":    "results/weather/wind-visibility-good/entry_result_ZONE-001.txt",
        "Weather · Wind Medium":  "results/weather/wind-visibility-medium/entry_result_ZONE-001.txt",
        "Weather · Wind Bad":     "results/weather/wind-visibility-bad/entry_result_ZONE-001.txt",
        "MFC/Payload · Good":     "results/mfc-payload/mfc-payload-good/entry_result_ZONE-001.txt",
        "MFC/Payload · Medium":   "results/mfc-payload/mfc-payload-medium/entry_result_ZONE-001.txt",
        "MFC/Payload · Bad":      "results/mfc-payload/mfc-payload-bad/entry_result_ZONE-001.txt",
    }

    def __init__(self, initial_file: Optional[str] = None):
        super().__init__()
        self.setWindowTitle("SADE – Agent Architecture Visualizer")
        self.resize(1280, 780)
        self._setup_palette()
        self._build_ui()
        if initial_file:
            self._load_file(initial_file)

    # ── palette ───────────────────────────────────────────────────────────────

    def _setup_palette(self):
        pal = QPalette()
        pal.setColor(QPalette.Window,          _qc(BG))
        pal.setColor(QPalette.WindowText,      _qc(TXT_HI))
        pal.setColor(QPalette.Base,            _qc(CARD_BG))
        pal.setColor(QPalette.AlternateBase,   _qc(PANEL_BG))
        pal.setColor(QPalette.Text,            _qc(TXT_HI))
        pal.setColor(QPalette.Button,          _qc(CARD_BG))
        pal.setColor(QPalette.ButtonText,      _qc(TXT_HI))
        pal.setColor(QPalette.Highlight,       _qc(ACCENT))
        pal.setColor(QPalette.HighlightedText, _qc("#FFFFFF"))
        self.setPalette(pal)
        self.setStyleSheet(f"QMainWindow {{ background: {BG}; }}")

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        root = QWidget()
        root_layout = QVBoxLayout(root)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)
        self.setCentralWidget(root)

        # ── header bar ──
        header = self._make_header()
        root_layout.addWidget(header)

        # ── main splitter ──
        splitter = QSplitter(Qt.Horizontal)
        splitter.setHandleWidth(1)
        splitter.setStyleSheet(f"""
            QSplitter::handle {{ background: {BORDER}; }}
        """)
        root_layout.addWidget(splitter, 1)

        # left: diagram
        left = QWidget()
        left.setMinimumWidth(280)
        left.setStyleSheet(f"background: {PANEL_BG};")
        ll = QVBoxLayout(left)
        ll.setContentsMargins(16, 16, 16, 16)
        ll.setSpacing(10)

        diag_label = QLabel("Agent Architecture")
        diag_label.setStyleSheet(
            f"color:{TXT_MED};font-size:11px;font-weight:600;"
            f"text-transform:uppercase;letter-spacing:1px;margin-bottom:4px;"
        )
        ll.addWidget(diag_label)

        self.diagram = ArchitectureDiagram()
        self.diagram.node_clicked.connect(self._on_node_clicked)
        ll.addWidget(self.diagram, 1)

        hint = QLabel("Click a node to jump to its tab →")
        hint.setStyleSheet(f"color:{TXT_LO};font-size:10px;margin-top:4px;")
        hint.setAlignment(Qt.AlignCenter)
        ll.addWidget(hint)

        splitter.addWidget(left)

        # right: detail tabs
        right = QWidget()
        right.setStyleSheet(f"background: {BG};")
        rl = QVBoxLayout(right)
        rl.setContentsMargins(12, 14, 14, 12)

        self.tabs = DetailTabs()
        rl.addWidget(self.tabs, 1)

        splitter.addWidget(right)
        splitter.setSizes([360, 900])

        # ── status bar ──
        self.status = QStatusBar()
        self.status.setStyleSheet(
            f"QStatusBar {{ background:{PANEL_BG};color:{TXT_MED};"
            f"border-top:1px solid {BORDER};font-size:11px;padding:2px 8px; }}"
        )
        self.setStatusBar(self.status)
        self.status.showMessage("No result loaded — use the dropdown or Browse to open a file.")

    def _make_header(self) -> QWidget:
        bar = QWidget()
        bar.setStyleSheet(
            f"background:{PANEL_BG};border-bottom:1px solid {BORDER};"
        )
        bar.setFixedHeight(60)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(20, 0, 20, 0)
        layout.setSpacing(10)

        # title
        title = QLabel("SADE-ZONE")
        title.setStyleSheet(
            f"color:{ACCENT};font-size:18px;font-weight:900;letter-spacing:2px;"
        )
        sub = QLabel("System for Entry Decision Evaluation for UAVs")
        sub.setStyleSheet(f"color:{TXT_MED};font-size:11px;")
        layout.addWidget(title)
        layout.addSpacing(4)
        layout.addWidget(sub)
        layout.addStretch()

        # preset combo
        self.combo = QComboBox()
        self.combo.setStyleSheet(_COMBO_STYLE)
        self.combo.addItem("— select a preset —")
        for name in self.PRESETS:
            self.combo.addItem(name)
        self.combo.currentTextChanged.connect(self._on_preset_changed)
        layout.addWidget(self.combo)

        # browse button
        btn_browse = QPushButton("Browse…")
        btn_browse.setStyleSheet(_BTN_STYLE)
        btn_browse.clicked.connect(self._on_browse)
        layout.addWidget(btn_browse)

        # clear button
        btn_clear = QPushButton("Clear")
        btn_clear.setStyleSheet(_BTN_STYLE)
        btn_clear.clicked.connect(self._on_clear)
        layout.addWidget(btn_clear)

        return bar

    # ── slots ─────────────────────────────────────────────────────────────────

    def _on_node_clicked(self, tab_idx: int):
        self.tabs.setCurrentIndex(tab_idx)

    def _on_preset_changed(self, name: str):
        if name in self.PRESETS:
            path = Path(__file__).parent / self.PRESETS[name]
            self._load_file(str(path))

    def _on_browse(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Open Result File",
            str(Path(__file__).parent / "results"),
            "Text / JSON Files (*.txt *.json);;All Files (*)",
        )
        if path:
            self._load_file(path)

    def _on_clear(self):
        self.diagram.clear()
        self.tabs.clear()
        self.combo.setCurrentIndex(0)
        self.status.showMessage("Cleared.")

    def _load_file(self, filepath: str):
        try:
            text = Path(filepath).read_text()
            # Result files have a header block then JSON — find first '{'
            json_start = text.find("{")
            if json_start == -1:
                raise ValueError("No JSON object found in file.")
            data = json.loads(text[json_start:])

            self.diagram.load_result(data)
            self.tabs.load_result(data)

            dtype  = data.get("decision", {}).get("type", "?")
            dcolor = decision_color(dtype)
            name   = Path(filepath).name
            self.status.showMessage(
                f"Loaded: {filepath}    |    Decision: {dtype}"
            )
            # jump to decision tab
            self.tabs.setCurrentIndex(4)

        except Exception as exc:
            self.status.showMessage(f"Error loading {filepath}: {exc}")


# ─── Entry Point ──────────────────────────────────────────────────────────────

def main():
    app = QApplication(sys.argv)
    app.setApplicationName("SADE Visualizer")

    # allow a file path as CLI arg
    initial = sys.argv[1] if len(sys.argv) > 1 else None
    win = SADEWindow(initial_file=initial)
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
