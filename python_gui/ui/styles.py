"""
H-Walker GUI Design System
Glassmorphism dark theme - centralized color palette and stylesheet

Design by CBJ
"""

# === Color Palette (Grafana/Linear inspired) ===
C = {
    'bg':      '#0D0D0F',
    'sidebar': '#0F0F18',
    'card':    '#1A1A24',
    'hover':   '#22223A',
    'border':  'rgba(255,255,255,0.06)',
    'text1':   '#E2E8F0',
    'text2':   '#94A3B8',
    'muted':   '#64748B',
    'blue':    '#4C9EFF',
    'teal':    '#2DD4BF',
    'purple':  '#A78BFA',
    'amber':   '#FCD34D',
    'red':     '#F87171',
    'green':   '#4ADE80',
    'orange':  '#FB923C',
    'pink':    '#F472B6',
}

# === Series colors for multi-file comparison ===
SERIES_COLORS = [
    '#4C9EFF', '#2DD4BF', '#A78BFA', '#FB923C', '#F472B6',
    '#FCD34D', '#4ADE80', '#F87171', '#818CF8', '#22D3EE',
]

SERIES_STYLES = ['Solid', 'Dash', 'Dot', 'DashDot']

# === All CSV columns (67 columns from firmware) ===
ALL_COLUMNS = [
    "L_DesForce_N", "L_ActForce_N", "L_ErrForce_N",
    "L_DesVel_mps", "L_ActVel_mps", "L_ErrVel_mps",
    "L_DesPos_deg", "L_ActPos_deg", "L_ErrPos_deg",
    "L_DesCurr_A", "L_ActCurr_A", "L_ErrCurr_A",
    "R_DesForce_N", "R_ActForce_N", "R_ErrForce_N",
    "R_DesVel_mps", "R_ActVel_mps", "R_ErrVel_mps",
    "R_DesPos_deg", "R_ActPos_deg", "R_ErrPos_deg",
    "R_DesCurr_A", "R_ActCurr_A", "R_ErrCurr_A",
    "L_GCP", "R_GCP", "L_Pitch", "R_Pitch",
    "L_Roll", "R_Roll", "L_Yaw", "R_Yaw",
    "L_Gx", "L_Gy", "L_Gz", "R_Gx", "R_Gy", "R_Gz",
    "L_Ax", "L_Ay", "L_Az", "R_Ax", "R_Ay", "R_Az",
    "L_Event", "R_Event", "L_Phase", "R_Phase",
    "L_StepTime", "R_StepTime", "L_HO_GCP", "R_HO_GCP",
    "L_AdmVel_mps", "R_AdmVel_mps",
    "L_MotionFF_mps", "R_MotionFF_mps",
    "L_TreadmillFF_mps", "R_TreadmillFF_mps",
    "TFF_Gain", "FF_Gain_F", "Mode", "Mark",
]


def get_stylesheet() -> str:
    """Global Glassmorphism stylesheet for the entire application"""
    return f"""
        * {{ font-family: "Inter","SF Pro Display","Segoe UI",sans-serif; }}
        QMainWindow, #Central {{ background:{C['bg']}; color:{C['text1']}; }}

        /* === TopNav === */
        #TopNav {{
            background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 {C['sidebar']}, stop:1 rgba(15,15,24,0.95));
            border-bottom:1px solid rgba(255,255,255,0.03);
        }}
        #TopNav QPushButton {{
            background:transparent; border:none; border-bottom:2px solid transparent;
            color:{C['muted']}; font-size:13px; font-weight:600; padding:4px 14px;
        }}
        #TopNav QPushButton:hover {{
            color:{C['text2']};
            background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 transparent, stop:1 rgba(255,255,255,0.02));
        }}
        #TopNav QPushButton:checked {{
            color:{C['blue']}; border-bottom:2px solid {C['blue']};
            background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 transparent, stop:1 rgba(76,158,255,0.06));
        }}

        /* === Sidebar === */
        #Sidebar {{ background:{C['sidebar']}; border:none; border-right:1px solid rgba(255,255,255,0.04); }}
        #SidebarInner {{ background:{C['sidebar']}; }}

        /* === Glass Cards === */
        #GlassCard {{
            background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(255,255,255,0.04), stop:1 rgba(255,255,255,0.02));
            border:1px solid rgba(255,255,255,0.07); border-radius:10px;
        }}
        #GlassCard:hover {{
            background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(255,255,255,0.06), stop:1 rgba(255,255,255,0.03));
            border:1px solid rgba(255,255,255,0.12);
            border-left:2px solid rgba(76,158,255,0.3);
        }}
        #MetricCard {{
            background:rgba(255,255,255,0.03); border:1px solid rgba(255,255,255,0.07); border-radius:8px;
        }}

        /* === Chart Area === */
        #ChartArea {{
            background:#13131A; border:1px solid rgba(255,255,255,0.06); border-radius:8px;
        }}

        /* === Buttons === */
        #AccentBtn {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #3B82F6,stop:0.5 #2563EB,stop:1 #1D4ED8);
            color:white; border:none; border-radius:6px; padding:7px 14px; font-weight:600; font-size:12px;
        }}
        #AccentBtn:hover {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #60A5FA,stop:0.5 #3B82F6,stop:1 #2563EB);
        }}
        #AccentBtn:pressed {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #1D4ED8,stop:1 #1E40AF);
            padding:8px 14px 6px 14px;
        }}
        #SecondaryBtn {{
            background:rgba(255,255,255,0.06); color:{C['text2']}; border:1px solid rgba(255,255,255,0.1);
            border-radius:6px; padding:7px 14px; font-weight:500;
        }}
        #SecondaryBtn:hover {{ background:rgba(255,255,255,0.1); color:{C['text1']}; }}
        #SecondaryBtn:pressed {{ background:rgba(255,255,255,0.04); }}
        #GreenBtn {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #22C55E,stop:0.5 #16A34A,stop:1 #15803D);
            color:white; border:none; border-radius:6px; padding:7px 12px; font-weight:600;
        }}
        #GreenBtn:hover {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #4ADE80,stop:0.5 #22C55E,stop:1 #16A34A);
        }}
        #GreenBtn:pressed {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #15803D,stop:1 #166534);
        }}
        #RedBtn {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #EF4444,stop:0.5 #DC2626,stop:1 #B91C1C);
            color:white; border:none; border-radius:6px; padding:7px 12px; font-weight:600;
        }}
        #RedBtn:hover {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #F87171,stop:0.5 #EF4444,stop:1 #DC2626);
        }}
        #RedBtn:pressed {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #B91C1C,stop:1 #991B1B);
        }}
        #SmallBtn {{
            background:rgba(255,255,255,0.06); color:{C['text2']}; border:1px solid rgba(255,255,255,0.08);
            border-radius:4px; padding:3px 7px; font-size:11px;
        }}
        #SmallBtn:hover {{ background:rgba(255,255,255,0.1); }}
        #SmallBtn:pressed {{ background:rgba(255,255,255,0.03); }}

        /* === Toolbar Buttons (MATLAB-style zoom/pan) === */
        #ToolbarBtn {{
            background:rgba(255,255,255,0.06); color:{C['text2']};
            border:1px solid rgba(255,255,255,0.1); border-radius:5px;
            padding:3px 10px; font-size:11px; font-weight:500;
        }}
        #ToolbarBtn:hover {{ background:rgba(255,255,255,0.1); }}
        #ToolbarBtn:checked {{
            background:rgba(76,158,255,0.15); color:{C['blue']};
            border:1px solid rgba(76,158,255,0.4);
        }}
        #CloseBtn {{ background:transparent; color:{C['muted']}; border:none; font-size:11px; }}
        #CloseBtn:hover {{ color:{C['red']}; }}

        /* === Inputs === */
        QComboBox, QSpinBox, QDoubleSpinBox, QLineEdit {{
            background:rgba(255,255,255,0.05); border:1px solid rgba(255,255,255,0.1);
            border-radius:6px; padding:5px 8px; color:{C['text1']}; font-size:12px; min-height:18px;
        }}
        QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus {{
            border:1px solid rgba(76,158,255,0.5);
            background:rgba(76,158,255,0.04);
        }}
        QComboBox::drop-down {{ border:none; width:20px; }}
        QComboBox QAbstractItemView {{
            background:{C['card']}; color:{C['text1']}; selection-background-color:{C['hover']};
            border:1px solid rgba(255,255,255,0.1);
        }}
        #SearchInput {{
            background:rgba(255,255,255,0.04); border:1px solid rgba(255,255,255,0.08);
            border-radius:6px; padding:5px 8px; color:{C['text2']};
        }}

        /* === Tabs === */
        QTabWidget::pane {{
            border:1px solid rgba(255,255,255,0.06);
            background:{C['bg']}; border-radius:0 0 8px 8px;
        }}
        QTabBar::tab {{
            background:transparent; color:{C['muted']}; padding:9px 18px;
            border:none; border-bottom:2px solid transparent;
            font-size:12px; font-weight:600; letter-spacing:0.3px;
        }}
        QTabBar::tab:selected {{
            color:{C['blue']}; border-bottom:2px solid {C['blue']};
            background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(76,158,255,0.10), stop:1 transparent);
        }}
        QTabBar::tab:hover {{
            color:{C['text2']};
            background:qlineargradient(x1:0,y1:0,x2:0,y2:1,
                stop:0 rgba(255,255,255,0.03), stop:1 transparent);
        }}

        /* === Labels & Checks === */
        QLabel {{ color:{C['text2']}; font-size:12px; }}
        QRadioButton, QCheckBox {{ color:{C['text2']}; font-size:12px; spacing:5px; }}

        /* === Log === */
        #LogText {{
            background:rgba(0,0,0,0.3); border:1px solid rgba(255,255,255,0.04);
            border-radius:6px; color:{C['muted']}; font-family:"JetBrains Mono","Menlo",monospace;
            font-size:10px; padding:4px;
        }}

        /* === File Table === */
        #FileTable {{
            background:{C['card']}; border:1px solid rgba(255,255,255,0.06); border-radius:8px;
            gridline-color:rgba(255,255,255,0.04); color:{C['text1']}; font-size:12px;
            selection-background-color:rgba(76,158,255,0.15); selection-color:{C['text1']};
        }}
        #FileTable::item {{ padding:5px 8px; }}
        QHeaderView::section {{
            background:rgba(255,255,255,0.04); color:{C['muted']}; border:none;
            border-bottom:1px solid rgba(255,255,255,0.06); padding:6px; font-weight:600; font-size:11px;
        }}

        /* === Progress Bar === */
        QProgressBar {{
            background:rgba(255,255,255,0.05); border:none; border-radius:4px;
            text-align:center; color:{C['muted']}; font-size:10px;
        }}
        QProgressBar::chunk {{
            background:qlineargradient(x1:0,y1:0,x2:1,y2:0,stop:0 #3B82F6,stop:1 #2DD4BF);
            border-radius:4px;
        }}

        /* === Status Bar === */
        QStatusBar {{
            background:{C['sidebar']}; color:{C['muted']}; border-top:1px solid rgba(255,255,255,0.04);
            font-size:11px; padding:2px 8px;
        }}

        /* === Scrollbars === */
        QScrollBar:vertical {{ background:transparent; width:5px; margin:4px 0; }}
        QScrollBar::handle:vertical {{ background:rgba(255,255,255,0.1); border-radius:2px; min-height:30px; }}
        QScrollBar::handle:vertical:hover {{ background:rgba(255,255,255,0.18); }}
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height:0; }}
        QScrollBar:horizontal {{ height:0; }}
    """
