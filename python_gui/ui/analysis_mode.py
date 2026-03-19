"""
H-Walker Analysis Mode

CSV 데이터 분석: Chart, Gait Analysis, Compare
- 다중 파일 로드 (자동 색+선스타일 배정)
- MATLAB-style Zoom/Pan 툴바
- Gait Analysis: HS 감지 + GCP-normalized profile (mean±SD)
- Compare: Normalize by stride + 다중 컬럼 오버레이
- 편집 가능한 축 레이블
"""

import os
import numpy as np
from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QFrame, QLabel,
    QPushButton, QScrollArea, QCheckBox, QLineEdit,
    QTabWidget, QComboBox, QRadioButton, QFileDialog,
    QDoubleSpinBox, QTableWidget, QTableWidgetItem,
    QHeaderView, QAbstractItemView, QSplitter
)
from PyQt5.QtCore import Qt, pyqtSignal
import pyqtgraph as pg

from ui.styles import C, SERIES_COLORS, SERIES_STYLES, ALL_COLUMNS


PEN_STYLES = [Qt.SolidLine, Qt.DashLine, Qt.DotLine, Qt.DashDotLine]


def _section_label(text: str) -> QLabel:
    lbl = QLabel(text.upper())
    lbl.setStyleSheet(
        f"color:{C['muted']}; font-size:9px; font-weight:700; "
        f"letter-spacing:1px; background:transparent; border:none;"
    )
    return lbl


class AnalysisMode(QWidget):
    """Analysis mode - CSV data visualization, gait analysis, comparison"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._loaded_files = []      # [(path, color, linestyle_idx, df), ...]
        self._selected_columns = set()
        self._column_checkboxes = {}
        self._x_axis_mode = 'index'
        self._line_width = 2.0
        self._legend_size = '11pt'
        self._subplot_mode = False
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)

        # === Left Sidebar ===
        left = QScrollArea()
        left.setFixedWidth(255)
        left.setWidgetResizable(True)
        left.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        left.setObjectName("Sidebar")

        sidebar = QWidget()
        sidebar.setObjectName("SidebarInner")
        sl = QVBoxLayout(sidebar)
        sl.setSpacing(6)
        sl.setContentsMargins(6, 6, 6, 6)

        # --- FILES ---
        files_card = QFrame()
        files_card.setObjectName("GlassCard")
        fl = QVBoxLayout(files_card)
        fl.setContentsMargins(10, 10, 10, 10)
        fl.setSpacing(6)

        fl.addWidget(_section_label("Files"))

        open_btn = QPushButton("+ Open CSV")
        open_btn.setObjectName("AccentBtn")
        open_btn.clicked.connect(self._open_csv)
        fl.addWidget(open_btn)

        self._file_list_layout = QVBoxLayout()
        fl.addLayout(self._file_list_layout)

        note = QLabel("Colors & line styles auto-assigned\nper file (up to 10 files)")
        note.setStyleSheet(f"color:{C['muted']}; font-size:9px; background:transparent; border:none;")
        fl.addWidget(note)

        add_more = QPushButton("+ Add more")
        add_more.setStyleSheet(
            f"color:{C['muted']}; background:transparent; border:1px dashed rgba(255,255,255,0.08); "
            f"border-radius:6px; padding:5px; font-size:11px;"
        )
        add_more.clicked.connect(self._open_csv)
        fl.addWidget(add_more)
        sl.addWidget(files_card)

        # --- COLUMNS ---
        cols_card = QFrame()
        cols_card.setObjectName("GlassCard")
        col_l = QVBoxLayout(cols_card)
        col_l.setContentsMargins(10, 10, 10, 10)
        col_l.setSpacing(4)

        col_l.addWidget(_section_label("Plot Columns"))

        self._search_input = QLineEdit()
        self._search_input.setPlaceholderText("Search...")
        self._search_input.setObjectName("SearchInput")
        self._search_input.textChanged.connect(self._filter_columns)
        col_l.addWidget(self._search_input)

        col_scroll = QScrollArea()
        col_scroll.setMaximumHeight(220)
        col_scroll.setWidgetResizable(True)
        col_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        col_scroll.setStyleSheet("border:none; background:transparent;")

        self._col_container = QWidget()
        self._col_container.setStyleSheet("background:transparent;")
        self._col_layout = QVBoxLayout(self._col_container)
        self._col_layout.setSpacing(1)
        self._col_layout.setContentsMargins(0, 0, 0, 0)

        for col_name in ALL_COLUMNS:
            cb = QCheckBox(col_name)
            cb.setStyleSheet(f"color:{C['text2']}; font-size:11px; background:transparent;")
            cb.toggled.connect(lambda checked, name=col_name: self._on_column_toggled(name, checked))
            self._col_layout.addWidget(cb)
            self._column_checkboxes[col_name] = cb

        col_scroll.setWidget(self._col_container)
        col_l.addWidget(col_scroll)
        sl.addWidget(cols_card)

        # --- X AXIS ---
        x_card = QFrame()
        x_card.setObjectName("GlassCard")
        xl = QVBoxLayout(x_card)
        xl.setContentsMargins(10, 8, 10, 8)
        xl.addWidget(_section_label("X Axis"))

        self._x_index_radio = QRadioButton("Sample Index")
        self._x_index_radio.setChecked(True)
        self._x_index_radio.toggled.connect(lambda: self._set_x_axis('index'))
        xl.addWidget(self._x_index_radio)

        self._x_gcp_radio = QRadioButton("GCP (%)")
        self._x_gcp_radio.toggled.connect(lambda checked: self._set_x_axis('gcp') if checked else None)
        xl.addWidget(self._x_gcp_radio)
        sl.addWidget(x_card)

        sl.addStretch()

        # --- EXPORT ---
        exp_card = QFrame()
        exp_card.setObjectName("GlassCard")
        el = QHBoxLayout(exp_card)
        el.setContentsMargins(10, 8, 10, 8)
        for fmt in ["PNG", "SVG"]:
            b = QPushButton(f"Export {fmt}")
            b.setObjectName("SecondaryBtn")
            b.clicked.connect(lambda checked, f=fmt: self._export_chart(f))
            el.addWidget(b)
        sl.addWidget(exp_card)

        left.setWidget(sidebar)
        layout.addWidget(left)

        # === Right Tabs ===
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.setContentsMargins(0, 0, 0, 0)
        rl.setSpacing(0)

        self._tabs = QTabWidget()
        self._tabs.setObjectName("AnalysisTabs")

        self._tabs.addTab(self._create_chart_tab(), "Chart")
        self._tabs.addTab(self._create_gait_tab(), "Gait Analysis")
        self._tabs.addTab(self._create_compare_tab(), "Compare")

        rl.addWidget(self._tabs)
        layout.addWidget(right, 1)

    # ================================================================
    # ZOOM/PAN TOOLBAR (shared by all tabs)
    # ================================================================

    def _create_zoom_toolbar(self, plot_widget: pg.PlotWidget, with_y_lock=True,
                              with_line_controls=False) -> QFrame:
        """MATLAB-style toolbar: Zoom/Pan/Reset + Y-axis lock + Line width + Legend size"""
        bar = QFrame()
        bar.setObjectName("GlassCard")
        bar.setFixedHeight(34)
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(8, 0, 8, 0)
        bl.setSpacing(4)

        zoom_btn = QPushButton("Zoom")
        zoom_btn.setCheckable(True)
        zoom_btn.setChecked(True)
        zoom_btn.setObjectName("ToolbarBtn")

        pan_btn = QPushButton("Pan")
        pan_btn.setCheckable(True)
        pan_btn.setObjectName("ToolbarBtn")

        reset_btn = QPushButton("Reset")
        reset_btn.setObjectName("SmallBtn")

        vb = plot_widget.getViewBox()
        vb.setMouseMode(pg.ViewBox.RectMode)

        def set_zoom():
            zoom_btn.setChecked(True)
            pan_btn.setChecked(False)
            vb.setMouseMode(pg.ViewBox.RectMode)

        def set_pan():
            pan_btn.setChecked(True)
            zoom_btn.setChecked(False)
            vb.setMouseMode(pg.ViewBox.PanMode)

        def reset_view():
            plot_widget.enableAutoRange()

        zoom_btn.clicked.connect(set_zoom)
        pan_btn.clicked.connect(set_pan)
        reset_btn.clicked.connect(reset_view)

        bl.addWidget(zoom_btn)
        bl.addWidget(pan_btn)
        bl.addWidget(reset_btn)

        if with_y_lock:
            bl.addSpacing(8)
            bl.addWidget(QLabel("Y:"))

            y_min_spin = QDoubleSpinBox()
            y_min_spin.setRange(-100000, 100000)
            y_min_spin.setValue(-100)
            y_min_spin.setFixedWidth(65)
            bl.addWidget(y_min_spin)

            bl.addWidget(QLabel("~"))

            y_max_spin = QDoubleSpinBox()
            y_max_spin.setRange(-100000, 100000)
            y_max_spin.setValue(100)
            y_max_spin.setFixedWidth(65)
            bl.addWidget(y_max_spin)

            lock_btn = QPushButton("Lock Y")
            lock_btn.setCheckable(True)
            lock_btn.setObjectName("ToolbarBtn")

            def toggle_y_lock(checked):
                if checked:
                    y_min, y_max = y_min_spin.value(), y_max_spin.value()
                    if y_min < y_max:
                        plot_widget.setYRange(y_min, y_max, padding=0)
                        vb.setMouseEnabled(x=True, y=False)
                        lock_btn.setText("Y Locked")
                else:
                    vb.setMouseEnabled(x=True, y=True)
                    plot_widget.enableAutoRange(axis='y')
                    lock_btn.setText("Lock Y")

            lock_btn.clicked.connect(toggle_y_lock)
            bl.addWidget(lock_btn)

        # Line width + Legend size controls (Chart tab only)
        if with_line_controls:
            bl.addSpacing(8)

            lw_label = QLabel("W:")
            lw_label.setStyleSheet(f"color:{C['muted']}; font-size:9px; background:transparent; border:none;")
            bl.addWidget(lw_label)

            lw_spin = QDoubleSpinBox()
            lw_spin.setRange(0.5, 6.0)
            lw_spin.setSingleStep(0.5)
            lw_spin.setValue(self._line_width)
            lw_spin.setFixedWidth(55)
            lw_spin.valueChanged.connect(self._on_linewidth_changed)
            bl.addWidget(lw_spin)

            lg_label = QLabel("Lg:")
            lg_label.setStyleSheet(f"color:{C['muted']}; font-size:9px; background:transparent; border:none;")
            bl.addWidget(lg_label)

            lg_combo = QComboBox()
            lg_combo.addItems(["8pt", "10pt", "11pt", "13pt", "15pt"])
            lg_combo.setCurrentText(self._legend_size)
            lg_combo.setFixedWidth(60)
            lg_combo.currentTextChanged.connect(self._on_legend_size_changed)
            bl.addWidget(lg_combo)

        bl.addStretch()
        return bar

    def _on_linewidth_changed(self, val):
        self._line_width = val
        self._update_chart()
        self._update_compare()

    def _on_legend_size_changed(self, size_text):
        self._legend_size = size_text
        self._update_chart()

    # ================================================================
    # AXIS LABEL EDITOR
    # ================================================================

    def _create_label_editors(self, plot_widget: pg.PlotWidget) -> QFrame:
        """Editable title, xlabel, ylabel for a chart"""
        bar = QFrame()
        bl = QHBoxLayout(bar)
        bl.setContentsMargins(4, 2, 4, 2)
        bl.setSpacing(8)

        # Y label
        yl = QLineEdit()
        yl.setPlaceholderText("Y label")
        yl.setObjectName("SearchInput")
        yl.setFixedWidth(80)
        yl.textChanged.connect(lambda t: plot_widget.setLabel('left', t))
        bl.addWidget(yl)

        # Title
        tl = QLineEdit()
        tl.setPlaceholderText("Chart Title")
        tl.setObjectName("SearchInput")
        tl.setAlignment(Qt.AlignCenter)
        tl.textChanged.connect(lambda t: plot_widget.setTitle(t, color=C['text1'], size='11pt'))
        bl.addWidget(tl, 1)

        # X label
        xl_input = QLineEdit()
        xl_input.setPlaceholderText("X label")
        xl_input.setObjectName("SearchInput")
        xl_input.setFixedWidth(80)
        xl_input.textChanged.connect(lambda t: plot_widget.setLabel('bottom', t))
        bl.addWidget(xl_input)

        return bar

    # ================================================================
    # CHART TAB
    # ================================================================

    def _create_chart_tab(self) -> QWidget:
        w = QWidget()
        self._chart_layout = QVBoxLayout(w)
        self._chart_layout.setSpacing(4)
        self._chart_layout.setContentsMargins(4, 4, 4, 4)

        # Legend bar
        self._chart_legend = QFrame()
        self._chart_legend.setObjectName("GlassCard")
        self._chart_legend.setFixedHeight(26)
        self._legend_layout = QHBoxLayout(self._chart_legend)
        self._legend_layout.setContentsMargins(10, 0, 10, 0)
        self._legend_layout.addStretch()
        self._chart_layout.addWidget(self._chart_legend)

        # Toolbar (with line width + legend controls)
        self._chart_plot = pg.PlotWidget()
        self._chart_plot.showGrid(x=True, y=True, alpha=0.3)
        self._chart_plot.setMouseEnabled(x=True, y=True)
        self._chart_plot.enableAutoRange()
        self._chart_pg_legend = self._chart_plot.addLegend(
            offset=(10, 10), labelTextSize=self._legend_size)

        self._chart_layout.addWidget(
            self._create_zoom_toolbar(self._chart_plot, with_line_controls=True))

        # Plot
        self._chart_layout.addWidget(self._chart_plot, 1)

        # Label editors
        self._chart_layout.addWidget(self._create_label_editors(self._chart_plot))

        # MATLAB command input
        cmd_bar = QFrame()
        cmd_bar.setObjectName("GlassCard")
        cmd_bar.setFixedHeight(30)
        cmd_layout = QHBoxLayout(cmd_bar)
        cmd_layout.setContentsMargins(8, 0, 8, 0)
        cmd_prompt = QLabel(">>")
        cmd_prompt.setStyleSheet(
            f"color:{C['blue']}; font-size:12px; font-weight:700; "
            f"font-family:monospace; background:transparent; border:none;")
        cmd_layout.addWidget(cmd_prompt)
        self._cmd_input = QLineEdit()
        self._cmd_input.setPlaceholderText(
            "ylim [-10 120]  |  xlim [0 5000]  |  grid on  |  title \"text\"  |  linewidth 3")
        self._cmd_input.setStyleSheet(
            f"background:transparent; border:none; color:{C['text1']}; "
            f"font-family:monospace; font-size:11px;")
        self._cmd_input.returnPressed.connect(self._execute_command)
        cmd_layout.addWidget(self._cmd_input, 1)
        self._chart_layout.addWidget(cmd_bar)

        return w

    # ================================================================
    # MATLAB COMMAND EXECUTION
    # ================================================================

    def _execute_command(self):
        """Execute MATLAB-style command on current chart"""
        cmd = self._cmd_input.text().strip()
        self._cmd_input.clear()
        if not cmd:
            return

        plot = self._get_current_plot()
        parts = cmd.split()
        verb = parts[0].lower()

        try:
            if verb == 'ylim' and len(parts) >= 3:
                vals = [float(x.strip('[](),')) for x in parts[1:] if x.strip('[](),')]
                if len(vals) >= 2:
                    plot.setYRange(vals[0], vals[1], padding=0)

            elif verb == 'xlim' and len(parts) >= 3:
                vals = [float(x.strip('[](),')) for x in parts[1:] if x.strip('[](),')]
                if len(vals) >= 2:
                    plot.setXRange(vals[0], vals[1], padding=0)

            elif verb == 'grid':
                on = len(parts) < 2 or parts[1].lower() in ('on', 'true', '1')
                plot.showGrid(x=on, y=on, alpha=0.3 if on else 0)

            elif verb == 'title' and len(parts) >= 2:
                title = ' '.join(parts[1:]).strip('"\'')
                plot.setTitle(title, color=C['text1'], size='11pt')

            elif verb == 'ylabel' and len(parts) >= 2:
                label = ' '.join(parts[1:]).strip('"\'')
                plot.setLabel('left', label)

            elif verb == 'xlabel' and len(parts) >= 2:
                label = ' '.join(parts[1:]).strip('"\'')
                plot.setLabel('bottom', label)

            elif verb == 'linewidth' and len(parts) >= 2:
                self._line_width = float(parts[1])
                self._update_chart()

            elif verb == 'legend' and len(parts) >= 2:
                size = parts[1] if 'pt' in parts[1] else f'{parts[1]}pt'
                self._legend_size = size
                self._update_chart()

            elif verb == 'auto' or verb == 'autorange':
                plot.enableAutoRange()

            elif verb == 'help':
                self._cmd_input.setPlaceholderText(
                    "ylim/xlim [min max] | grid on/off | title/ylabel/xlabel \"text\" "
                    "| linewidth N | legend Npt | auto")
        except (ValueError, IndexError):
            pass

    # ================================================================
    # GAIT ANALYSIS TAB
    # ================================================================

    def _create_gait_tab(self) -> QWidget:
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.setSpacing(6)
        wl.setContentsMargins(6, 6, 6, 6)

        splitter = QSplitter(Qt.Vertical)

        # --- TOP: Gait Parameters Table ---
        table_frame = QWidget()
        tfl = QVBoxLayout(table_frame)
        tfl.setContentsMargins(0, 0, 0, 0)
        tfl.setSpacing(4)

        table_title = QLabel("GAIT PARAMETERS")
        table_title.setStyleSheet(
            f"color:{C['muted']}; font-size:10px; font-weight:700; "
            f"letter-spacing:1px; background:transparent; border:none;"
        )
        tfl.addWidget(table_title)

        self._gait_table = QTableWidget()
        self._gait_table.setObjectName("FileTable")
        self._gait_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._gait_table.setSelectionMode(QAbstractItemView.NoSelection)
        self._gait_table.verticalHeader().setVisible(False)
        self._gait_table.horizontalHeader().setStretchLastSection(True)
        self._gait_table.setAlternatingRowColors(False)
        self._gait_table.setStyleSheet(
            f"QTableWidget {{ background:{C['card']}; border:1px solid rgba(255,255,255,0.06); "
            f"border-radius:8px; gridline-color:rgba(255,255,255,0.04); "
            f"color:{C['text1']}; font-size:12px; }}"
            f"QTableWidget::item {{ padding:4px 8px; }}"
        )
        tfl.addWidget(self._gait_table)
        splitter.addWidget(table_frame)

        # --- BOTTOM: Force Profile Plot ---
        plot_frame = QWidget()
        pfl = QVBoxLayout(plot_frame)
        pfl.setContentsMargins(0, 0, 0, 0)
        pfl.setSpacing(4)

        self._gait_plot = pg.PlotWidget(title="GCP-Normalized Force Profile")
        self._gait_plot.showGrid(x=True, y=True, alpha=0.3)
        self._gait_plot.setLabel('bottom', 'GCP (%)')
        self._gait_plot.setLabel('left', 'Force (N)')
        self._gait_plot.enableAutoRange()
        self._gait_plot.addLegend(offset=(10, 10))

        pfl.addWidget(self._create_zoom_toolbar(self._gait_plot))
        pfl.addWidget(self._gait_plot, 1)
        splitter.addWidget(plot_frame)

        splitter.setSizes([300, 400])
        wl.addWidget(splitter, 1)

        return w

    # ================================================================
    # COMPARE TAB
    # ================================================================

    def _create_compare_tab(self) -> QWidget:
        w = QWidget()
        wl = QVBoxLayout(w)
        wl.setSpacing(6)
        wl.setContentsMargins(6, 6, 6, 6)

        # Controls bar
        ctrl = QFrame()
        ctrl.setObjectName("GlassCard")
        ctrl.setFixedHeight(44)
        ctl = QHBoxLayout(ctrl)
        ctl.setContentsMargins(10, 0, 10, 0)

        ctl.addWidget(QLabel("X:"))
        self._cmp_x_combo = QComboBox()
        self._cmp_x_combo.addItems(["Sample Index", "GCP (%)"])
        self._cmp_x_combo.setFixedWidth(120)
        self._cmp_x_combo.currentIndexChanged.connect(self._update_compare)
        ctl.addWidget(self._cmp_x_combo)

        ctl.addSpacing(8)

        self._normalize_cb = QCheckBox("Normalize by stride")
        self._normalize_cb.setStyleSheet(f"color:{C['text2']}; background:transparent;")
        self._normalize_cb.toggled.connect(self._update_compare)
        ctl.addWidget(self._normalize_cb)

        ctl.addStretch()

        hint = QLabel("Select columns below to compare across files")
        hint.setStyleSheet(f"color:{C['muted']}; font-size:10px; background:transparent; border:none;")
        ctl.addWidget(hint)
        wl.addWidget(ctrl)

        self._cmp_checkboxes = {}

        # Scrollable column bar for many columns
        cmp_scroll = QScrollArea()
        cmp_scroll.setWidgetResizable(True)
        cmp_scroll.setFixedHeight(70)
        cmp_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        cmp_scroll.setStyleSheet("border:none; background:transparent;")

        cmp_inner = QWidget()
        cmp_inner.setStyleSheet("background:transparent;")
        cmp_grid = QVBoxLayout(cmp_inner)
        cmp_grid.setContentsMargins(0, 0, 0, 0)
        cmp_grid.setSpacing(2)

        # Group columns by category with L/R pairs
        cmp_groups = [
            ("Force", ["L_ActForce_N", "R_ActForce_N", "L_DesForce_N", "R_DesForce_N", "L_ErrForce_N", "R_ErrForce_N"]),
            ("GCP", ["L_GCP", "R_GCP"]),
            ("IMU", ["L_Pitch", "R_Pitch", "L_Roll", "R_Roll", "L_Yaw", "R_Yaw"]),
            ("Gyro", ["L_Gy", "R_Gy", "L_Gx", "R_Gx", "L_Gz", "R_Gz"]),
            ("Velocity", ["L_ActVel_mps", "R_ActVel_mps", "L_DesVel_mps", "R_DesVel_mps"]),
            ("Position", ["L_ActPos_deg", "R_ActPos_deg", "L_DesPos_deg", "R_DesPos_deg"]),
            ("Current", ["L_ActCurr_A", "R_ActCurr_A", "L_DesCurr_A", "R_DesCurr_A"]),
            ("Gait", ["L_Event", "R_Event", "L_Phase", "R_Phase", "L_StepTime", "R_StepTime"]),
            ("FF", ["L_MotionFF_mps", "R_MotionFF_mps", "L_TreadmillFF_mps", "R_TreadmillFF_mps", "TFF_Gain"]),
        ]
        for group_name, cols in cmp_groups:
            row = QHBoxLayout()
            row.setSpacing(6)
            glabel = QLabel(f"{group_name}:")
            glabel.setFixedWidth(50)
            glabel.setStyleSheet(
                f"color:{C['muted']}; font-size:9px; font-weight:700; background:transparent; border:none;"
            )
            row.addWidget(glabel)
            for col_name in cols:
                cb = QCheckBox(col_name.replace("_mps", "").replace("_deg", "").replace("_N", "").replace("_A", ""))
                cb.setToolTip(col_name)
                cb.setStyleSheet(f"color:{C['text2']}; font-size:10px; background:transparent;")
                cb.toggled.connect(self._update_compare)
                row.addWidget(cb)
                self._cmp_checkboxes[col_name] = cb
            row.addStretch()
            cmp_grid.addLayout(row)

        cmp_scroll.setWidget(cmp_inner)
        wl.addWidget(cmp_scroll)

        # File legend
        self._cmp_file_bar = QFrame()
        self._cmp_file_bar.setObjectName("GlassCard")
        self._cmp_file_bar.setFixedHeight(28)
        self._cmp_file_layout = QHBoxLayout(self._cmp_file_bar)
        self._cmp_file_layout.setContentsMargins(10, 0, 10, 0)
        self._cmp_file_layout.addStretch()
        wl.addWidget(self._cmp_file_bar)

        # Compare chart
        self._compare_plot = pg.PlotWidget()
        self._compare_plot.showGrid(x=True, y=True, alpha=0.3)
        self._compare_plot.setMouseEnabled(x=True, y=True)
        self._compare_plot.enableAutoRange()
        self._compare_plot.addLegend(offset=(10, 10))

        wl.addWidget(self._create_zoom_toolbar(self._compare_plot))
        wl.addWidget(self._compare_plot, 1)
        wl.addWidget(self._create_label_editors(self._compare_plot))

        return w

    # ================================================================
    # DATA OPERATIONS
    # ================================================================

    def _open_csv(self):
        paths, _ = QFileDialog.getOpenFileNames(
            self, "Open CSV Files", "",
            "CSV Files (*.csv *.CSV);;All Files (*)"
        )
        for path in paths:
            self.load_file(path)

    def load_file(self, path: str):
        """Load a CSV file and add to file list"""
        try:
            import pandas as pd
        except ImportError:
            return

        if any(f[0] == path for f in self._loaded_files):
            return
        if len(self._loaded_files) >= 10:
            return
        try:
            df = pd.read_csv(path)
            df.columns = df.columns.str.strip()  # 컬럼명 공백 제거
        except Exception:
            return

        idx = len(self._loaded_files)
        color = SERIES_COLORS[idx % len(SERIES_COLORS)]
        style_idx = idx % len(PEN_STYLES)

        self._loaded_files.append((path, color, style_idx, df))

        # Auto-select common columns on first file
        if len(self._loaded_files) == 1:
            self._auto_select_columns(df)

        self._update_file_list_ui()
        self._update_chart()
        self._update_gait_analysis()
        self._update_compare()

    def _auto_select_columns(self, df):
        """Auto-check common L/R column pairs on first CSV load"""
        auto_cols = ["L_ActForce_N", "R_ActForce_N", "L_GCP", "R_GCP"]
        for col in auto_cols:
            if col in df.columns and col in self._column_checkboxes:
                self._column_checkboxes[col].setChecked(True)
        # Auto-check compare defaults
        for col in ["L_ActForce_N", "R_ActForce_N", "L_GCP", "R_GCP"]:
            if col in self._cmp_checkboxes:
                self._cmp_checkboxes[col].setChecked(True)

    def _remove_file(self, path: str):
        self._loaded_files = [f for f in self._loaded_files if f[0] != path]
        updated = []
        for i, (p, _, _, df) in enumerate(self._loaded_files):
            color = SERIES_COLORS[i % len(SERIES_COLORS)]
            style_idx = i % len(PEN_STYLES)
            updated.append((p, color, style_idx, df))
        self._loaded_files = updated
        self._update_file_list_ui()
        self._update_chart()
        self._update_gait_analysis()
        self._update_compare()

    def _update_file_list_ui(self):
        while self._file_list_layout.count():
            item = self._file_list_layout.takeAt(0)
            if item.layout():
                while item.layout().count():
                    child = item.layout().takeAt(0)
                    if child.widget():
                        child.widget().deleteLater()
            elif item.widget():
                item.widget().deleteLater()

        style_chars = ["───", "- - -", "· · ·", "─ · ─"]
        for path, color, style_idx, df in self._loaded_files:
            row = QHBoxLayout()
            dot = QLabel(style_chars[style_idx])
            dot.setStyleSheet(
                f"color:{color}; font-size:11px; font-family:monospace; "
                f"background:transparent; border:none;"
            )
            row.addWidget(dot)
            name = QLabel(os.path.basename(path))
            name.setStyleSheet(f"color:{C['text1']}; font-size:11px; background:transparent; border:none;")
            row.addWidget(name)
            row.addStretch()
            x_btn = QPushButton("✕")
            x_btn.setFixedSize(18, 18)
            x_btn.setObjectName("CloseBtn")
            x_btn.clicked.connect(lambda checked, p=path: self._remove_file(p))
            row.addWidget(x_btn)
            self._file_list_layout.addLayout(row)

        self._update_compare_legend()

    def _filter_columns(self, text: str):
        text = text.lower()
        for name, cb in self._column_checkboxes.items():
            cb.setVisible(text in name.lower() if text else True)

    def _on_column_toggled(self, col_name: str, checked: bool):
        if checked:
            self._selected_columns.add(col_name)
        else:
            self._selected_columns.discard(col_name)
        self._update_chart()

    def _set_x_axis(self, mode: str):
        self._x_axis_mode = mode
        self._update_chart()

    # ================================================================
    # CHART UPDATE
    # ================================================================

    def _update_chart(self):
        self._chart_plot.clear()
        # Recreate legend with current size
        self._chart_pg_legend = self._chart_plot.addLegend(
            offset=(10, 10), labelTextSize=self._legend_size)

        if not self._loaded_files or not self._selected_columns:
            return

        # Rebuild custom legend bar
        while self._legend_layout.count() > 1:
            item = self._legend_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        lw = self._line_width

        for path, color, style_idx, df in self._loaded_files:
            pen_style = PEN_STYLES[style_idx]
            fname = os.path.basename(path)

            for col_name in self._selected_columns:
                if col_name not in df.columns:
                    continue
                y_data = df[col_name].values.astype(np.float64)
                if self._x_axis_mode == 'gcp' and 'L_GCP' in df.columns:
                    x_data = df['L_GCP'].values.astype(np.float64) * 100
                else:
                    x_data = np.arange(len(y_data), dtype=np.float64)

                pen = pg.mkPen(color, width=lw, style=pen_style)
                self._chart_plot.plot(x_data, y_data, pen=pen, name=f"{fname}: {col_name}")

            # Legend bar entry
            dot_lbl = QLabel(f"● {fname}")
            dot_lbl.setStyleSheet(
                f"color:{color}; font-size:11px; font-weight:600; background:transparent; border:none;"
            )
            self._legend_layout.insertWidget(self._legend_layout.count() - 1, dot_lbl)

        self._chart_plot.enableAutoRange()

    # ================================================================
    # GAIT ANALYSIS
    # ================================================================

    def _update_gait_analysis(self):
        """Compute gait parameters table + GCP-normalized force profile"""
        self._gait_plot.clear()

        if not self._loaded_files:
            self._gait_table.setRowCount(0)
            self._gait_table.setColumnCount(0)
            return

        # Collect gait parameters for all files
        all_file_params = []
        x_pct = np.linspace(0, 100, 101)

        for file_idx, (path, color, style_idx, df) in enumerate(self._loaded_files):
            fname = os.path.basename(path)
            params = self._compute_gait_params(df)
            params['_fname'] = fname
            params['_color'] = color
            params['_style_idx'] = style_idx
            all_file_params.append(params)

            # Plot force profile
            if params.get('l_force_strides') is not None:
                self._plot_stride_band(params['l_force_strides'], x_pct,
                                       color, f"L ({fname})", alpha_fill=40)
            r_color = SERIES_COLORS[(file_idx * 2 + 1) % len(SERIES_COLORS)]
            if params.get('r_force_strides') is not None:
                self._plot_stride_band(params['r_force_strides'], x_pct,
                                       r_color, f"R ({fname})", alpha_fill=30)

        self._gait_plot.enableAutoRange()

        # Build parameter table
        self._build_gait_table(all_file_params)

    @staticmethod
    def _estimate_sample_rate(df) -> float:
        """Estimate sample rate from CSV. Uses time column if available, else default 111Hz."""
        for col in ['Time', 'time', 'Time_s', 'Timestamp']:
            if col in df.columns:
                t = df[col].values.astype(np.float64)
                dt = np.median(np.diff(t))
                if dt > 0:
                    return 1.0 / dt
        # Default: firmware streams at 111Hz (9ms loop)
        return 111.0

    def _compute_gait_params(self, df) -> dict:
        """Compute all gait parameters from a single CSV file"""
        p = {}
        sample_rate = self._estimate_sample_rate(df)

        for side, prefix in [('L', 'l'), ('R', 'r')]:
            gcp_col = f'{side}_GCP'
            force_col = f'{side}_ActForce_N'

            if gcp_col not in df.columns:
                continue

            gcp = df[gcp_col].values.astype(np.float64)
            gcp_range = np.ptp(gcp)

            # No data check: GCP 변동 없음 = IMU 미사용
            if gcp_range < 0.5:
                p[f'{prefix}_no_data'] = True
                p[f'{prefix}_hs_count'] = 0
                p[f'{prefix}_stride_count'] = 0
                p[f'{prefix}_ho_count'] = 0
                p[f'{prefix}_stride_time_mean'] = 0
                p[f'{prefix}_stride_time_std'] = 0
                p[f'{prefix}_step_time_mean'] = 0
                p[f'{prefix}_step_time_std'] = 0
                p[f'{prefix}_cadence'] = 0
                p[f'{prefix}_stance_mean'] = 0
                p[f'{prefix}_stance_std'] = 0
                p[f'{prefix}_swing_mean'] = 0
                p[f'{prefix}_swing_std'] = 0
                p[f'{prefix}_peak_force_mean'] = 0
                p[f'{prefix}_peak_force_std'] = 0
                p[f'{prefix}_mean_force_mean'] = 0
                p[f'{prefix}_mean_force_std'] = 0
                p[f'{prefix}_force_strides'] = None
                continue

            if np.max(gcp) > 2:
                gcp = gcp / 100.0
                gcp_range = gcp_range / 100.0

            # HS detection: 동적 threshold (signal range의 40%, 최소 0.3)
            hs_threshold = -max(0.3, gcp_range * 0.4)
            diffs = np.diff(gcp)
            hs_idx = np.where(diffs < hs_threshold)[0] + 1
            n_strides = max(0, len(hs_idx) - 1)

            p[f'{prefix}_hs_count'] = len(hs_idx)
            p[f'{prefix}_stride_count'] = n_strides

            # HO detection
            ho_count = 0
            for i in range(n_strides):
                s, e = hs_idx[i], hs_idx[i + 1]
                if np.any(gcp[s:e] > 0.6):
                    ho_count += 1
            p[f'{prefix}_ho_count'] = ho_count

            # Stride times (s)
            stride_times = []
            for i in range(n_strides):
                stride_times.append((hs_idx[i + 1] - hs_idx[i]) / sample_rate)
            stride_times = np.array(stride_times) if stride_times else np.array([])
            p[f'{prefix}_stride_time_mean'] = np.mean(stride_times) if len(stride_times) > 0 else 0
            p[f'{prefix}_stride_time_std'] = np.std(stride_times) if len(stride_times) > 0 else 0

            # Step time (half stride ≈ stride/2 for single side)
            p[f'{prefix}_step_time_mean'] = p[f'{prefix}_stride_time_mean'] / 2 if p[f'{prefix}_stride_time_mean'] > 0 else 0
            p[f'{prefix}_step_time_std'] = p[f'{prefix}_stride_time_std'] / 2 if p[f'{prefix}_stride_time_std'] > 0 else 0

            # Cadence (steps/min)
            if p[f'{prefix}_stride_time_mean'] > 0:
                p[f'{prefix}_cadence'] = 60.0 / p[f'{prefix}_stride_time_mean'] * 2  # 2 steps per stride
            else:
                p[f'{prefix}_cadence'] = 0

            # Stance / Swing phase (% of stride)
            stance_ratios = []
            swing_ratios = []
            for i in range(n_strides):
                s, e = hs_idx[i], hs_idx[i + 1]
                stride_gcp = gcp[s:e]
                n_total = len(stride_gcp)
                if n_total < 5:
                    continue
                # Stance = GCP < 0.6, Swing = GCP >= 0.6 (simplified)
                n_stance = np.sum(stride_gcp < 0.6)
                stance_ratios.append(n_stance / n_total * 100)
                swing_ratios.append((n_total - n_stance) / n_total * 100)
            p[f'{prefix}_stance_mean'] = np.mean(stance_ratios) if stance_ratios else 0
            p[f'{prefix}_stance_std'] = np.std(stance_ratios) if stance_ratios else 0
            p[f'{prefix}_swing_mean'] = np.mean(swing_ratios) if swing_ratios else 0
            p[f'{prefix}_swing_std'] = np.std(swing_ratios) if swing_ratios else 0

            # Peak force per stride
            if force_col in df.columns:
                force = df[force_col].values.astype(np.float64)
                peak_forces = []
                mean_forces = []
                strides_data = []

                for i in range(n_strides):
                    s, e = hs_idx[i], hs_idx[i + 1]
                    if e - s < 10:
                        continue
                    stride_f = force[s:e]
                    peak_forces.append(np.max(stride_f))
                    mean_forces.append(np.mean(stride_f))
                    # Resample for plot
                    x_orig = np.linspace(0, 100, len(stride_f))
                    strides_data.append(np.interp(np.linspace(0, 100, 101), x_orig, stride_f))

                p[f'{prefix}_peak_force_mean'] = np.mean(peak_forces) if peak_forces else 0
                p[f'{prefix}_peak_force_std'] = np.std(peak_forces) if peak_forces else 0
                p[f'{prefix}_mean_force_mean'] = np.mean(mean_forces) if mean_forces else 0
                p[f'{prefix}_mean_force_std'] = np.std(mean_forces) if mean_forces else 0
                p[f'{prefix}_force_strides'] = np.array(strides_data) if strides_data else None
            else:
                p[f'{prefix}_peak_force_mean'] = 0
                p[f'{prefix}_peak_force_std'] = 0
                p[f'{prefix}_mean_force_mean'] = 0
                p[f'{prefix}_mean_force_std'] = 0
                p[f'{prefix}_force_strides'] = None

        # Symmetry Index (SI) — only if both sides have data
        l_st = p.get('l_stride_time_mean', 0)
        r_st = p.get('r_stride_time_mean', 0)
        if l_st > 0 and r_st > 0:
            p['symmetry_index'] = abs(l_st - r_st) / ((l_st + r_st) / 2) * 100
        else:
            p['symmetry_index'] = 0  # N/A if one side missing

        # Gait velocity estimation (if treadmill speed available)
        # Use stride_time and approximate stride_length from position data
        p['total_strides'] = p.get('l_stride_count', 0) + p.get('r_stride_count', 0)
        l_cad = p.get('l_cadence', 0)
        r_cad = p.get('r_cadence', 0)
        p['avg_cadence'] = (l_cad + r_cad) / 2 if (l_cad + r_cad) > 0 else 0

        return p

    def _build_gait_table(self, all_file_params: list):
        """Build the gait parameter table with Mean ± SD for each file"""
        if not all_file_params:
            self._gait_table.setRowCount(0)
            return

        # Define rows: (display_name, lambda to get (mean, std) or value from params)
        param_rows = [
            ("Total Strides", lambda p: f"{p.get('l_stride_count',0) + p.get('r_stride_count',0)}"),
            ("HS Count (L / R)", lambda p: f"{p.get('l_hs_count',0)} / {p.get('r_hs_count',0)}"),
            ("HO Count (L / R)", lambda p: f"{p.get('l_ho_count',0)} / {p.get('r_ho_count',0)}"),
            ("", None),  # separator
            ("Stride Time L (s)", lambda p: self._fmt_ms(p, 'l_stride_time')),
            ("Stride Time R (s)", lambda p: self._fmt_ms(p, 'r_stride_time')),
            ("Step Time L (s)", lambda p: self._fmt_ms(p, 'l_step_time')),
            ("Step Time R (s)", lambda p: self._fmt_ms(p, 'r_step_time')),
            ("", None),
            ("Cadence L (steps/min)", lambda p: f"{p.get('l_cadence',0):.1f}"),
            ("Cadence R (steps/min)", lambda p: f"{p.get('r_cadence',0):.1f}"),
            ("Avg Cadence (steps/min)", lambda p: f"{p.get('avg_cadence',0):.1f}"),
            ("", None),
            ("Stance Phase L (%)", lambda p: self._fmt_ms(p, 'l_stance')),
            ("Stance Phase R (%)", lambda p: self._fmt_ms(p, 'r_stance')),
            ("Swing Phase L (%)", lambda p: self._fmt_ms(p, 'l_swing')),
            ("Swing Phase R (%)", lambda p: self._fmt_ms(p, 'r_swing')),
            ("", None),
            ("Peak Force L (N)", lambda p: self._fmt_ms(p, 'l_peak_force')),
            ("Peak Force R (N)", lambda p: self._fmt_ms(p, 'r_peak_force')),
            ("Mean Force L (N)", lambda p: self._fmt_ms(p, 'l_mean_force')),
            ("Mean Force R (N)", lambda p: self._fmt_ms(p, 'r_mean_force')),
            ("", None),
            ("Symmetry Index (%)", lambda p: f"{p.get('symmetry_index',0):.1f}"),
        ]

        n_files = len(all_file_params)
        col_headers = ["Parameter"] + [p['_fname'] for p in all_file_params]
        self._gait_table.setColumnCount(len(col_headers))
        self._gait_table.setHorizontalHeaderLabels(col_headers)
        self._gait_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        for i in range(1, len(col_headers)):
            self._gait_table.horizontalHeader().setSectionResizeMode(i, QHeaderView.ResizeToContents)

        # Filter out separator rows for counting
        rows = [r for r in param_rows if r[1] is not None]
        self._gait_table.setRowCount(len(param_rows))

        row_idx = 0
        for name, getter in param_rows:
            if getter is None:
                # Separator row
                sep_item = QTableWidgetItem("")
                sep_item.setFlags(Qt.NoItemFlags)
                self._gait_table.setItem(row_idx, 0, sep_item)
                self._gait_table.setRowHeight(row_idx, 8)
                for c in range(1, len(col_headers)):
                    self._gait_table.setItem(row_idx, c, QTableWidgetItem(""))
                row_idx += 1
                continue

            item = QTableWidgetItem(name)
            item.setForeground(pg.mkColor(C['text2']))
            self._gait_table.setItem(row_idx, 0, item)

            for fi, params in enumerate(all_file_params):
                try:
                    val_str = getter(params)
                except Exception:
                    val_str = "—"
                val_item = QTableWidgetItem(val_str)
                val_item.setTextAlignment(Qt.AlignCenter)
                val_item.setForeground(pg.mkColor(C['text1']))
                self._gait_table.setItem(row_idx, fi + 1, val_item)

            row_idx += 1

    @staticmethod
    def _fmt_ms(p: dict, key: str) -> str:
        """Format mean ± std from params dict. Distinguish No Data vs 0 strides"""
        prefix = key.split('_')[0]  # 'l' or 'r'
        if p.get(f'{prefix}_no_data'):
            return "No GCP"
        m = p.get(f'{key}_mean', 0)
        s = p.get(f'{key}_std', 0)
        if m == 0 and s == 0:
            return "0 strides"
        return f"{m:.2f} ± {s:.2f}"

    def _plot_stride_band(self, strides, x_pct, color, name, alpha_fill=40):
        """Plot mean ± SD shaded band for stride data"""
        mean = np.mean(strides, axis=0)
        std = np.std(strides, axis=0)

        # SD band
        upper_data = mean + std
        lower_data = mean - std

        upper_curve = pg.PlotDataItem(x_pct, upper_data, pen=pg.mkPen(None))
        lower_curve = pg.PlotDataItem(x_pct, lower_data, pen=pg.mkPen(None))

        from PyQt5.QtGui import QColor
        fill_color = QColor(color)
        fill_color.setAlpha(alpha_fill)

        fill = pg.FillBetweenItem(upper_curve, lower_curve, brush=fill_color)
        self._gait_plot.addItem(upper_curve)
        self._gait_plot.addItem(lower_curve)
        self._gait_plot.addItem(fill)

        # Mean line
        pen = pg.mkPen(color, width=2)
        self._gait_plot.plot(x_pct, mean, pen=pen, name=name)

    # ================================================================
    # COMPARE
    # ================================================================

    def _update_compare(self):
        self._compare_plot.clear()
        if not self._loaded_files:
            return

        selected = [name for name, cb in self._cmp_checkboxes.items() if cb.isChecked()]
        if not selected:
            return

        normalize = self._normalize_cb.isChecked()
        use_gcp_x = self._cmp_x_combo.currentIndex() == 1

        for path, color, style_idx, df in self._loaded_files:
            pen_style = PEN_STYLES[style_idx]
            fname = os.path.basename(path)

            if normalize:
                self._plot_normalized_compare(df, selected, color, pen_style, fname)
            else:
                for col_name in selected:
                    if col_name not in df.columns:
                        continue
                    y_data = df[col_name].values.astype(np.float64)
                    if use_gcp_x and 'L_GCP' in df.columns:
                        x_data = df['L_GCP'].values.astype(np.float64) * 100
                    else:
                        x_data = np.arange(len(y_data), dtype=np.float64)
                    pen = pg.mkPen(color, width=self._line_width, style=pen_style)
                    self._compare_plot.plot(x_data, y_data, pen=pen,
                                           name=f"{fname}: {col_name}")

        self._compare_plot.enableAutoRange()

    def _plot_normalized_compare(self, df, selected_cols, color, pen_style, fname=""):
        """Plot stride-normalized data for compare tab (mean±SD band + individual strides)"""
        # Determine GCP column for HS detection
        gcp_col = 'L_GCP' if 'L_GCP' in df.columns else ('R_GCP' if 'R_GCP' in df.columns else None)
        if gcp_col is None:
            return

        gcp = df[gcp_col].values.astype(np.float64)
        if np.max(gcp) > 2:
            gcp = gcp / 100.0

        diffs = np.diff(gcp)
        boundaries = np.where(diffs < -0.5)[0] + 1

        x_pct = np.linspace(0, 100, 101)

        for col_name in selected_cols:
            if col_name not in df.columns:
                continue
            y = df[col_name].values.astype(np.float64)

            # Collect all stride profiles
            stride_profiles = []
            for i in range(len(boundaries) - 1):
                start, end = boundaries[i], boundaries[i + 1]
                if end - start < 10:
                    continue
                stride_y = y[start:end]
                x_orig = np.linspace(0, 100, len(stride_y))
                y_interp = np.interp(x_pct, x_orig, stride_y)
                stride_profiles.append(y_interp)

                # Individual stride (thin, semi-transparent)
                thin_pen = pg.mkPen(color, width=1, style=pen_style)
                thin_pen.setColor(pg.mkColor(color))
                c = pg.mkColor(color)
                c.setAlpha(60)
                thin_pen.setColor(c)
                self._compare_plot.plot(x_pct, y_interp, pen=thin_pen)

            # Mean±SD band
            if len(stride_profiles) >= 2:
                strides_arr = np.array(stride_profiles)
                mean = np.mean(strides_arr, axis=0)
                std = np.std(strides_arr, axis=0)

                # SD fill band
                from PyQt5.QtGui import QColor
                fill_color = QColor(color)
                fill_color.setAlpha(35)
                upper = pg.PlotDataItem(x_pct, mean + std, pen=pg.mkPen(None))
                lower = pg.PlotDataItem(x_pct, mean - std, pen=pg.mkPen(None))
                fill = pg.FillBetweenItem(upper, lower, brush=fill_color)
                self._compare_plot.addItem(upper)
                self._compare_plot.addItem(lower)
                self._compare_plot.addItem(fill)

                # Mean line (bold)
                self._compare_plot.plot(x_pct, mean,
                    pen=pg.mkPen(color, width=3, style=pen_style),
                    name=f"{fname}: {col_name} (n={len(stride_profiles)})")

    def _update_compare_legend(self):
        while self._cmp_file_layout.count() > 1:
            item = self._cmp_file_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        style_chars = ["───", "- - -", "· · ·", "─ · ─"]
        for path, color, style_idx, df in self._loaded_files:
            d = QLabel(f"● {os.path.basename(path)}")
            d.setStyleSheet(f"color:{color}; font-size:11px; font-weight:600; background:transparent; border:none;")
            self._cmp_file_layout.insertWidget(self._cmp_file_layout.count() - 1, d)

    # ================================================================
    # EXPORT
    # ================================================================

    def _get_current_plot(self) -> pg.PlotWidget:
        """Return the plot widget of the currently active tab"""
        idx = self._tabs.currentIndex()
        if idx == 1:
            return self._gait_plot
        elif idx == 2:
            return self._compare_plot
        return self._chart_plot

    def _export_chart(self, fmt: str):
        """Export the current tab's chart as PNG or SVG"""
        plot = self._get_current_plot()
        tab_name = self._tabs.tabText(self._tabs.currentIndex()).replace(" ", "_")
        default_name = f"{tab_name}.{fmt.lower()}"

        path, _ = QFileDialog.getSaveFileName(
            self, f"Export as {fmt}", default_name,
            f"{fmt} Files (*.{fmt.lower()});;All Files (*)"
        )
        if not path:
            return

        try:
            from pyqtgraph.exporters import ImageExporter, SVGExporter
            if fmt.upper() == 'SVG':
                exporter = SVGExporter(plot.plotItem)
            else:
                exporter = ImageExporter(plot.plotItem)
                exporter.parameters()['width'] = 1920
            exporter.export(path)
        except Exception:
            # Fallback: grab the widget as QPixmap
            from PyQt5.QtGui import QPixmap
            pixmap = plot.grab()
            if not path.lower().endswith(f'.{fmt.lower()}'):
                path += f'.{fmt.lower()}'
            pixmap.save(path)

        # Verify the file was saved
        if os.path.exists(path) and os.path.getsize(path) > 0:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.information(self, "Export Complete",
                f"Saved: {os.path.basename(path)}\n"
                f"Size: {os.path.getsize(path) / 1024:.0f} KB\n"
                f"Path: {path}")
        else:
            from PyQt5.QtWidgets import QMessageBox
            QMessageBox.warning(self, "Export Failed",
                f"Failed to save {fmt} file.\nPath: {path}")
