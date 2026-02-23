#!/usr/bin/env python3
"""
DiskAnalyzer - Analyseur d'espace disque style TreeSize
Application desktop Python avec interface moderne (customtkinter)

Usage:
    python DiskAnalyzer.py
    python DiskAnalyzer.py --path "D:\\" --exclude "C:\\Windows" --depth 6

Packaging en .exe:
    pip install pyinstaller
    pyinstaller --onefile --windowed --icon=icon.ico --name DiskAnalyzer DiskAnalyzer.py
"""

import os
import sys
import time
import json
import threading
import webbrowser
import argparse
import platform
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Optional

# --- Imports GUI ---
try:
    import customtkinter as ctk
except ImportError:
    print("Installation de customtkinter...")
    os.system(f"{sys.executable} -m pip install customtkinter")
    import customtkinter as ctk

import tkinter as tk
from tkinter import ttk, filedialog, messagebox


# =============================================================================
# DATA MODELS
# =============================================================================

@dataclass
class FileInfo:
    name: str
    path: str
    size: int
    extension: str
    modified: str
    parent: str


@dataclass
class FolderInfo:
    name: str
    path: str
    size: int = 0
    file_count: int = 0
    children: list = field(default_factory=list)


# =============================================================================
# DISK SCANNER (threaded)
# =============================================================================

class DiskScanner:
    """Moteur de scan asynchrone avec callbacks de progression."""

    def __init__(self):
        self.all_files: list[FileInfo] = []
        self.tree_data: Optional[FolderInfo] = None
        self.exclude_paths: list[str] = []
        self.max_depth: int = 5
        self.scanning: bool = False
        self.cancelled: bool = False
        self.scanned_dirs: int = 0
        self.scanned_files: int = 0
        self.total_size: int = 0
        self.current_dir: str = ""

        # Callbacks
        self.on_progress = None  # (scanned_dirs, scanned_files, total_size, current_dir)
        self.on_complete = None  # (tree_data, all_files, duration)
        self.on_error = None     # (error_msg)

    def is_excluded(self, path: str) -> bool:
        norm = os.path.normcase(os.path.normpath(path))
        for ex in self.exclude_paths:
            ex_norm = os.path.normcase(os.path.normpath(ex))
            if norm == ex_norm or norm.startswith(ex_norm + os.sep):
                return True
        return False

    def scan_folder(self, folder_path: str, depth: int = 0) -> Optional[FolderInfo]:
        if self.cancelled:
            return None
        if self.is_excluded(folder_path):
            return None

        folder_name = os.path.basename(folder_path) or folder_path
        if depth == 0:
            folder_name = folder_path

        info = FolderInfo(name=folder_name, path=folder_path)
        self.scanned_dirs += 1
        self.current_dir = folder_path

        # Notify progress every 50 dirs
        if self.on_progress and self.scanned_dirs % 50 == 0:
            self.on_progress(self.scanned_dirs, self.scanned_files, self.total_size, self.current_dir)

        try:
            with os.scandir(folder_path) as entries:
                dirs = []
                for entry in entries:
                    if self.cancelled:
                        return None
                    try:
                        if entry.is_file(follow_symlinks=False):
                            stat = entry.stat(follow_symlinks=False)
                            fsize = stat.st_size
                            info.size += fsize
                            info.file_count += 1
                            self.scanned_files += 1
                            self.total_size += fsize

                            ext = os.path.splitext(entry.name)[1].lower()
                            try:
                                mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                            except (OSError, ValueError):
                                mtime = "N/A"

                            self.all_files.append(FileInfo(
                                name=entry.name,
                                path=entry.path,
                                size=fsize,
                                extension=ext if ext else "(aucune)",
                                modified=mtime,
                                parent=folder_path
                            ))

                        elif entry.is_dir(follow_symlinks=False):
                            if not self.is_excluded(entry.path):
                                dirs.append(entry.path)
                    except (PermissionError, OSError):
                        continue

                # Recurse into subdirectories
                if depth < self.max_depth:
                    for d in dirs:
                        child = self.scan_folder(d, depth + 1)
                        if child:
                            info.children.append(child)
                            info.size += child.size
                            info.file_count += child.file_count
                else:
                    # Beyond max depth: just count sizes recursively
                    for d in dirs:
                        self._count_deep(d, info)

        except (PermissionError, OSError):
            pass

        # Sort children by size desc
        info.children.sort(key=lambda c: c.size, reverse=True)
        return info

    def _count_deep(self, folder_path: str, parent_info: FolderInfo):
        """Comptage rapide au-dela de la profondeur max (pas de tree)."""
        if self.cancelled:
            return
        try:
            for root, dirs, files in os.walk(folder_path, followlinks=False):
                if self.cancelled:
                    return
                if self.is_excluded(root):
                    dirs.clear()
                    continue
                for f in files:
                    try:
                        fp = os.path.join(root, f)
                        stat = os.stat(fp, follow_symlinks=False)
                        fsize = stat.st_size
                        parent_info.size += fsize
                        parent_info.file_count += 1
                        self.scanned_files += 1
                        self.total_size += fsize

                        ext = os.path.splitext(f)[1].lower()
                        try:
                            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                        except (OSError, ValueError):
                            mtime = "N/A"

                        self.all_files.append(FileInfo(
                            name=f, path=fp, size=fsize,
                            extension=ext if ext else "(aucune)",
                            modified=mtime, parent=root
                        ))
                    except (PermissionError, OSError):
                        continue
                # Exclude filtered dirs
                dirs[:] = [d for d in dirs if not self.is_excluded(os.path.join(root, d))]
        except (PermissionError, OSError):
            pass

    def start(self, path: str, exclude_paths: list[str], max_depth: int):
        """Lance le scan dans un thread separé."""
        self.all_files = []
        self.tree_data = None
        self.exclude_paths = exclude_paths
        self.max_depth = max_depth
        self.scanning = True
        self.cancelled = False
        self.scanned_dirs = 0
        self.scanned_files = 0
        self.total_size = 0
        self.current_dir = ""

        def _run():
            start_time = time.time()
            try:
                self.tree_data = self.scan_folder(path, 0)
                duration = time.time() - start_time
                self.scanning = False
                if self.on_complete and not self.cancelled:
                    self.on_complete(self.tree_data, self.all_files, duration)
            except Exception as e:
                self.scanning = False
                if self.on_error:
                    self.on_error(str(e))

        thread = threading.Thread(target=_run, daemon=True)
        thread.start()

    def cancel(self):
        self.cancelled = True


# =============================================================================
# UTILITY FUNCTIONS
# =============================================================================

def format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 ** 4:
        return f"{size_bytes / (1024**4):.2f} To"
    if size_bytes >= 1024 ** 3:
        return f"{size_bytes / (1024**3):.2f} Go"
    if size_bytes >= 1024 ** 2:
        return f"{size_bytes / (1024**2):.2f} Mo"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.2f} Ko"
    return f"{size_bytes} o"


def size_color(size_bytes: int) -> str:
    """Retourne une couleur hex selon la taille."""
    if size_bytes >= 1024 ** 3:
        return "#ef4444"  # red
    if size_bytes >= 100 * 1024 ** 2:
        return "#f59e0b"  # orange
    if size_bytes >= 10 * 1024 ** 2:
        return "#3b82f6"  # blue
    if size_bytes >= 1024 ** 2:
        return "#10b981"  # green
    return "#6b7280"  # gray


# =============================================================================
# CUSTOM WIDGETS
# =============================================================================

class StyledTreeview(ttk.Treeview):
    """Treeview avec style sombre personnalisé."""

    @staticmethod
    def configure_style():
        style = ttk.Style()
        style.theme_use("default")

        # Treeview
        style.configure("Dark.Treeview",
            background="#1a2235",
            foreground="#e2e8f0",
            fieldbackground="#1a2235",
            borderwidth=0,
            font=("Segoe UI", 10),
            rowheight=28,
        )
        style.configure("Dark.Treeview.Heading",
            background="#111827",
            foreground="#8899b4",
            borderwidth=0,
            font=("Segoe UI", 9, "bold"),
            relief="flat",
        )
        style.map("Dark.Treeview",
            background=[("selected", "#2563eb")],
            foreground=[("selected", "#ffffff")],
        )
        style.map("Dark.Treeview.Heading",
            background=[("active", "#1e293b")],
        )

        # Remove borders
        style.layout("Dark.Treeview", [
            ('Dark.Treeview.treearea', {'sticky': 'nswe'})
        ])


class StatusBar(ctk.CTkFrame):
    """Barre de statut en bas de la fenetre."""

    def __init__(self, parent, **kwargs):
        super().__init__(parent, height=30, corner_radius=0, fg_color="#0f172a", **kwargs)
        self.pack_propagate(False)

        self.label = ctk.CTkLabel(self, text="Pret", font=("JetBrains Mono", 11),
                                   text_color="#5a6a85", anchor="w")
        self.label.pack(side="left", padx=12, fill="x", expand=True)

        self.size_label = ctk.CTkLabel(self, text="", font=("JetBrains Mono", 11),
                                        text_color="#5a6a85", anchor="e")
        self.size_label.pack(side="right", padx=12)

    def update_text(self, text: str, size_text: str = ""):
        self.label.configure(text=text)
        self.size_label.configure(text=size_text)


# =============================================================================
# MAIN APPLICATION
# =============================================================================

class DiskAnalyzerApp(ctk.CTk):
    """Application principale DiskAnalyzer."""

    def __init__(self, initial_path: str = "", exclude_paths: list = None, max_depth: int = 5):
        super().__init__()

        # --- Window setup ---
        self.title("DiskAnalyzer")
        self.geometry("1400x850")
        self.minsize(900, 600)
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        # Icon (optionnel)
        try:
            if platform.system() == "Windows":
                self.iconbitmap(default="")
        except:
            pass

        # --- State ---
        self.scanner = DiskScanner()
        self.scanner.on_progress = self._on_scan_progress
        self.scanner.on_complete = self._on_scan_complete
        self.scanner.on_error = self._on_scan_error
        self.tree_data: Optional[FolderInfo] = None
        self.all_files: list[FileInfo] = []
        self.top_files: list[FileInfo] = []
        self.ext_stats: list[dict] = []
        self.current_sort_col = "size"
        self.current_sort_reverse = True
        self.initial_path = initial_path
        self.initial_exclude = exclude_paths or []
        self.initial_depth = max_depth

        # --- Configure treeview style ---
        StyledTreeview.configure_style()

        # --- Build UI ---
        self._build_toolbar()
        self._build_tabs()
        self._build_status_bar()

        # --- Periodic UI update from scanner thread ---
        self._poll_interval = 100  # ms
        self._start_poll()

        # Auto-scan if path provided
        if self.initial_path:
            self.entry_path.delete(0, "end")
            self.entry_path.insert(0, self.initial_path)
            self.after(500, self._start_scan)

    # -------------------------------------------------------------------------
    # UI CONSTRUCTION
    # -------------------------------------------------------------------------

    def _build_toolbar(self):
        """Barre d'outils superieure."""
        toolbar = ctk.CTkFrame(self, height=60, corner_radius=0, fg_color="#0f172a",
                                border_width=0)
        toolbar.pack(fill="x", side="top")
        toolbar.pack_propagate(False)

        # Logo
        logo_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        logo_frame.pack(side="left", padx=16)

        logo_box = ctk.CTkFrame(logo_frame, width=36, height=36, corner_radius=8,
                                 fg_color="#3b82f6")
        logo_box.pack(side="left", padx=(0, 10))
        logo_box.pack_propagate(False)
        ctk.CTkLabel(logo_box, text="DA", font=("Segoe UI", 13, "bold"),
                      text_color="white").place(relx=0.5, rely=0.5, anchor="center")

        ctk.CTkLabel(logo_frame, text="DiskAnalyzer",
                      font=("Segoe UI", 17, "bold"), text_color="#e2e8f0"
                      ).pack(side="left")

        # Path input
        path_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        path_frame.pack(side="left", fill="x", expand=True, padx=20)

        ctk.CTkLabel(path_frame, text="Chemin :", font=("Segoe UI", 12),
                      text_color="#8899b4").pack(side="left", padx=(0, 8))

        self.entry_path = ctk.CTkEntry(path_frame, placeholder_text="C:\\",
                                         font=("JetBrains Mono", 12),
                                         height=34, corner_radius=6,
                                         fg_color="#1a2235", border_color="#2a3550",
                                         text_color="#e2e8f0")
        self.entry_path.pack(side="left", fill="x", expand=True)
        default_path = self.initial_path or ("C:\\" if platform.system() == "Windows" else "/")
        self.entry_path.insert(0, default_path)

        btn_browse = ctk.CTkButton(path_frame, text="...", width=40, height=34,
                                     corner_radius=6, font=("Segoe UI", 14, "bold"),
                                     fg_color="#2a3550", hover_color="#3b4a68",
                                     command=self._browse_folder)
        btn_browse.pack(side="left", padx=(6, 0))

        # Depth
        ctk.CTkLabel(path_frame, text="Prof:", font=("Segoe UI", 11),
                      text_color="#5a6a85").pack(side="left", padx=(16, 4))
        self.spin_depth = ctk.CTkEntry(path_frame, width=45, height=34, corner_radius=6,
                                         fg_color="#1a2235", border_color="#2a3550",
                                         font=("JetBrains Mono", 12),
                                         text_color="#e2e8f0", justify="center")
        self.spin_depth.pack(side="left")
        self.spin_depth.insert(0, str(self.initial_depth))

        # Exclude
        ctk.CTkLabel(path_frame, text="Exclure:", font=("Segoe UI", 11),
                      text_color="#5a6a85").pack(side="left", padx=(16, 4))
        self.entry_exclude = ctk.CTkEntry(path_frame, placeholder_text="C:\\Windows",
                                            font=("JetBrains Mono", 11),
                                            height=34, corner_radius=6, width=200,
                                            fg_color="#1a2235", border_color="#2a3550",
                                            text_color="#e2e8f0")
        self.entry_exclude.pack(side="left")
        default_exclude = ";".join(self.initial_exclude) if self.initial_exclude else (
            "C:\\Windows" if platform.system() == "Windows" else ""
        )
        self.entry_exclude.insert(0, default_exclude)

        # Buttons
        btn_frame = ctk.CTkFrame(toolbar, fg_color="transparent")
        btn_frame.pack(side="right", padx=16)

        self.btn_scan = ctk.CTkButton(btn_frame, text="  Analyser", width=120, height=36,
                                        corner_radius=8, font=("Segoe UI", 13, "bold"),
                                        fg_color="#2563eb", hover_color="#1d4ed8",
                                        command=self._start_scan)
        self.btn_scan.pack(side="left", padx=(0, 8))

        self.btn_cancel = ctk.CTkButton(btn_frame, text="Annuler", width=80, height=36,
                                          corner_radius=8, font=("Segoe UI", 12),
                                          fg_color="#7f1d1d", hover_color="#991b1b",
                                          command=self._cancel_scan, state="disabled")
        self.btn_cancel.pack(side="left", padx=(0, 8))

        self.btn_export = ctk.CTkButton(btn_frame, text="Exporter HTML", width=120, height=36,
                                          corner_radius=8, font=("Segoe UI", 12),
                                          fg_color="#1e3a5f", hover_color="#1e4976",
                                          command=self._export_html, state="disabled")
        self.btn_export.pack(side="left")

        # Progress bar (hidden by default)
        self.progress_frame = ctk.CTkFrame(self, height=3, corner_radius=0,
                                             fg_color="#111827")
        self.progress_frame.pack(fill="x", side="top")
        self.progress_frame.pack_propagate(False)

        self.progress_bar = ctk.CTkProgressBar(self.progress_frame, height=3,
                                                 corner_radius=0, mode="indeterminate",
                                                 progress_color="#3b82f6",
                                                 fg_color="#111827")
        self.progress_bar.pack(fill="x")
        self.progress_bar.set(0)

    def _build_tabs(self):
        """Systeme d'onglets principal."""
        self.tabview = ctk.CTkTabview(self, corner_radius=0, fg_color="#0a0e17",
                                        segmented_button_fg_color="#111827",
                                        segmented_button_selected_color="#2563eb",
                                        segmented_button_unselected_color="#1a2235",
                                        segmented_button_selected_hover_color="#1d4ed8",
                                        segmented_button_unselected_hover_color="#2a3550",
                                        text_color="#e2e8f0",
                                        border_width=0)
        self.tabview.pack(fill="both", expand=True, padx=0, pady=0)

        # --- Tab: Vue d'ensemble ---
        tab_overview = self.tabview.add("  Vue d'ensemble  ")
        self._build_overview_tab(tab_overview)

        # --- Tab: Arborescence ---
        tab_tree = self.tabview.add("  Arborescence  ")
        self._build_tree_tab(tab_tree)

        # --- Tab: Top Fichiers ---
        tab_files = self.tabview.add("  Top Fichiers  ")
        self._build_files_tab(tab_files)

        # --- Tab: Extensions ---
        tab_ext = self.tabview.add("  Extensions  ")
        self._build_extensions_tab(tab_ext)

    def _build_overview_tab(self, parent):
        """Onglet vue d'ensemble avec stats et top dossiers."""
        self.overview_frame = ctk.CTkScrollableFrame(parent, fg_color="#0a0e17",
                                                       corner_radius=0)
        self.overview_frame.pack(fill="both", expand=True)

        # Placeholder
        self.overview_placeholder = ctk.CTkLabel(
            self.overview_frame,
            text="Lancez une analyse pour voir les resultats",
            font=("Segoe UI", 16), text_color="#5a6a85"
        )
        self.overview_placeholder.pack(pady=100)

    def _build_tree_tab(self, parent):
        """Onglet arborescence interactive."""
        # Search bar
        search_frame = ctk.CTkFrame(parent, fg_color="#0a0e17", height=44, corner_radius=0)
        search_frame.pack(fill="x", padx=12, pady=(8, 0))

        self.tree_search = ctk.CTkEntry(search_frame, placeholder_text="Rechercher un dossier...",
                                          font=("Segoe UI", 12), height=34, corner_radius=6,
                                          fg_color="#1a2235", border_color="#2a3550",
                                          text_color="#e2e8f0")
        self.tree_search.pack(fill="x", padx=4, pady=4)
        self.tree_search.bind("<KeyRelease>", self._filter_tree)

        # Treeview
        tree_frame = ctk.CTkFrame(parent, fg_color="#1a2235", corner_radius=8)
        tree_frame.pack(fill="both", expand=True, padx=12, pady=8)

        columns = ("size", "files", "pct")
        self.folder_tree = StyledTreeview(tree_frame, columns=columns,
                                            style="Dark.Treeview", show="tree headings")
        self.folder_tree.heading("#0", text="Dossier", anchor="w")
        self.folder_tree.heading("size", text="Taille", anchor="e")
        self.folder_tree.heading("files", text="Fichiers", anchor="e")
        self.folder_tree.heading("pct", text="%", anchor="e")

        self.folder_tree.column("#0", width=500, minwidth=200)
        self.folder_tree.column("size", width=120, minwidth=80, anchor="e")
        self.folder_tree.column("files", width=100, minwidth=60, anchor="e")
        self.folder_tree.column("pct", width=70, minwidth=50, anchor="e")

        # Scrollbar
        tree_scroll = ttk.Scrollbar(tree_frame, orient="vertical",
                                      command=self.folder_tree.yview)
        self.folder_tree.configure(yscrollcommand=tree_scroll.set)
        tree_scroll.pack(side="right", fill="y")
        self.folder_tree.pack(fill="both", expand=True)

        # Double-click to open in Explorer
        self.folder_tree.bind("<Double-1>", self._open_folder_in_explorer)

    def _build_files_tab(self, parent):
        """Onglet top fichiers avec tableau triable."""
        # Search
        search_frame = ctk.CTkFrame(parent, fg_color="#0a0e17", height=44, corner_radius=0)
        search_frame.pack(fill="x", padx=12, pady=(8, 0))

        self.file_search = ctk.CTkEntry(search_frame,
                                          placeholder_text="Rechercher un fichier, extension, chemin...",
                                          font=("Segoe UI", 12), height=34, corner_radius=6,
                                          fg_color="#1a2235", border_color="#2a3550",
                                          text_color="#e2e8f0")
        self.file_search.pack(fill="x", padx=4, pady=4)
        self.file_search.bind("<KeyRelease>", self._filter_files)

        # Treeview table
        table_frame = ctk.CTkFrame(parent, fg_color="#1a2235", corner_radius=8)
        table_frame.pack(fill="both", expand=True, padx=12, pady=8)

        columns = ("name", "size", "ext", "modified", "path")
        self.file_table = StyledTreeview(table_frame, columns=columns,
                                           style="Dark.Treeview", show="headings")

        self.file_table.heading("name", text="Nom",
                                  command=lambda: self._sort_files("name"))
        self.file_table.heading("size", text="Taille",
                                  command=lambda: self._sort_files("size"))
        self.file_table.heading("ext", text="Extension",
                                  command=lambda: self._sort_files("ext"))
        self.file_table.heading("modified", text="Modifie",
                                  command=lambda: self._sort_files("modified"))
        self.file_table.heading("path", text="Chemin",
                                  command=lambda: self._sort_files("path"))

        self.file_table.column("name", width=280, minwidth=150)
        self.file_table.column("size", width=110, minwidth=80, anchor="e")
        self.file_table.column("ext", width=80, minwidth=50, anchor="center")
        self.file_table.column("modified", width=140, minwidth=100, anchor="center")
        self.file_table.column("path", width=500, minwidth=200)

        table_scroll = ttk.Scrollbar(table_frame, orient="vertical",
                                       command=self.file_table.yview)
        self.file_table.configure(yscrollcommand=table_scroll.set)
        table_scroll.pack(side="right", fill="y")
        self.file_table.pack(fill="both", expand=True)

        # Double-click to open containing folder
        self.file_table.bind("<Double-1>", self._open_file_location)

    def _build_extensions_tab(self, parent):
        """Onglet repartition par extension."""
        self.ext_frame = ctk.CTkScrollableFrame(parent, fg_color="#0a0e17",
                                                  corner_radius=0)
        self.ext_frame.pack(fill="both", expand=True)

        self.ext_placeholder = ctk.CTkLabel(
            self.ext_frame,
            text="Lancez une analyse pour voir la repartition par extension",
            font=("Segoe UI", 16), text_color="#5a6a85"
        )
        self.ext_placeholder.pack(pady=100)

    def _build_status_bar(self):
        """Barre de statut inferieure."""
        self.status_bar = StatusBar(self)
        self.status_bar.pack(fill="x", side="bottom")

    # -------------------------------------------------------------------------
    # ACTIONS
    # -------------------------------------------------------------------------

    def _browse_folder(self):
        folder = filedialog.askdirectory()
        if folder:
            self.entry_path.delete(0, "end")
            self.entry_path.insert(0, folder)

    def _start_scan(self):
        path = self.entry_path.get().strip()
        if not path or not os.path.isdir(path):
            messagebox.showerror("Erreur", f"Le chemin '{path}' n'existe pas ou n'est pas accessible.")
            return

        exclude_str = self.entry_exclude.get().strip()
        exclude_list = [e.strip() for e in exclude_str.split(";") if e.strip()]

        try:
            max_depth = int(self.spin_depth.get())
        except ValueError:
            max_depth = 5

        # UI state
        self.btn_scan.configure(state="disabled")
        self.btn_cancel.configure(state="normal")
        self.btn_export.configure(state="disabled")
        self.progress_bar.configure(mode="indeterminate")
        self.progress_bar.start()
        self.status_bar.update_text("Scan en cours...", "")

        # Clear previous data
        self._clear_results()

        # Start scanner
        self.scanner.start(path, exclude_list, max_depth)

    def _cancel_scan(self):
        self.scanner.cancel()
        self.btn_scan.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.status_bar.update_text("Analyse annulee.", "")

    def _clear_results(self):
        """Vide tous les onglets."""
        # Tree
        for item in self.folder_tree.get_children():
            self.folder_tree.delete(item)
        # Files table
        for item in self.file_table.get_children():
            self.file_table.delete(item)
        # Overview
        for widget in self.overview_frame.winfo_children():
            widget.destroy()
        # Extensions
        for widget in self.ext_frame.winfo_children():
            widget.destroy()

    # -------------------------------------------------------------------------
    # SCANNER CALLBACKS (called from thread -> use after() for thread safety)
    # -------------------------------------------------------------------------

    def _on_scan_progress(self, dirs, files, total_size, current_dir):
        # Truncate path for display
        display_dir = current_dir
        if len(display_dir) > 80:
            display_dir = "..." + display_dir[-77:]
        self.after(0, lambda: self.status_bar.update_text(
            f"Scan: {dirs:,} dossiers | {files:,} fichiers | {display_dir}",
            format_size(total_size)
        ))

    def _on_scan_complete(self, tree_data, all_files, duration):
        self.tree_data = tree_data
        self.all_files = all_files
        self.top_files = sorted(all_files, key=lambda f: f.size, reverse=True)[:500]

        # Extension stats
        ext_map = defaultdict(lambda: {"count": 0, "size": 0})
        for f in all_files:
            ext_map[f.extension]["count"] += 1
            ext_map[f.extension]["size"] += f.size
        self.ext_stats = sorted(
            [{"ext": k, "count": v["count"], "size": v["size"]} for k, v in ext_map.items()],
            key=lambda x: x["size"], reverse=True
        )[:40]

        # Update UI on main thread
        self.after(0, lambda: self._render_results(duration))

    def _on_scan_error(self, error_msg):
        self.after(0, lambda: messagebox.showerror("Erreur de scan", error_msg))
        self.after(0, lambda: self.btn_scan.configure(state="normal"))
        self.after(0, lambda: self.progress_bar.stop())

    # -------------------------------------------------------------------------
    # RENDERING
    # -------------------------------------------------------------------------

    def _render_results(self, duration: float):
        """Peuple tous les onglets avec les resultats du scan."""
        self.progress_bar.stop()
        self.progress_bar.set(0)
        self.btn_scan.configure(state="normal")
        self.btn_cancel.configure(state="disabled")
        self.btn_export.configure(state="normal")

        dur_str = f"{int(duration // 60)}m {int(duration % 60)}s" if duration >= 60 else f"{duration:.1f}s"
        self.status_bar.update_text(
            f"Analyse terminee en {dur_str} | "
            f"{self.scanner.scanned_dirs:,} dossiers | "
            f"{self.scanner.scanned_files:,} fichiers",
            format_size(self.scanner.total_size)
        )

        self._render_overview()
        self._render_tree()
        self._render_files()
        self._render_extensions()

    def _render_overview(self):
        """Vue d'ensemble avec cartes de stats et top dossiers."""
        parent = self.overview_frame
        for w in parent.winfo_children():
            w.destroy()

        if not self.tree_data:
            return

        # --- Stats cards ---
        cards_frame = ctk.CTkFrame(parent, fg_color="transparent")
        cards_frame.pack(fill="x", padx=8, pady=(8, 16))

        stats = [
            ("Espace analyse", format_size(self.tree_data.size), "#3b82f6"),
            ("Fichiers", f"{self.scanner.scanned_files:,}", "#06b6d4"),
            ("Dossiers", f"{self.scanner.scanned_dirs:,}", "#8b5cf6"),
            ("Plus gros fichier",
             format_size(self.top_files[0].size) if self.top_files else "N/A",
             "#f59e0b"),
            ("Extensions", str(len(self.ext_stats)), "#ec4899"),
        ]

        for i, (label, value, color) in enumerate(stats):
            card = ctk.CTkFrame(cards_frame, fg_color="#1a2235", corner_radius=10,
                                 border_width=1, border_color="#2a3550")
            card.pack(side="left", fill="x", expand=True, padx=4)

            ctk.CTkLabel(card, text=label.upper(), font=("Segoe UI", 10),
                          text_color="#5a6a85").pack(padx=16, pady=(14, 2), anchor="w")
            ctk.CTkLabel(card, text=value, font=("JetBrains Mono", 22, "bold"),
                          text_color=color).pack(padx=16, pady=(0, 4), anchor="w")

            if label == "Plus gros fichier" and self.top_files:
                name = self.top_files[0].name
                if len(name) > 30:
                    name = name[:27] + "..."
                ctk.CTkLabel(card, text=name, font=("Segoe UI", 10),
                              text_color="#5a6a85").pack(padx=16, pady=(0, 12), anchor="w")
            else:
                ctk.CTkLabel(card, text=" ", font=("Segoe UI", 10)).pack(padx=16, pady=(0, 12))

        # --- Top directories table ---
        if self.tree_data.children:
            ctk.CTkLabel(parent, text="Top dossiers par taille",
                          font=("Segoe UI", 15, "bold"), text_color="#e2e8f0",
                          anchor="w").pack(fill="x", padx=16, pady=(8, 8))

            top_dirs = self.tree_data.children[:20]
            max_size = top_dirs[0].size if top_dirs else 1

            for i, d in enumerate(top_dirs):
                row = ctk.CTkFrame(parent, fg_color="#1a2235" if i % 2 == 0 else "#151d2e",
                                     corner_radius=4, height=36)
                row.pack(fill="x", padx=12, pady=1)
                row.pack_propagate(False)

                # Rank
                ctk.CTkLabel(row, text=f"#{i+1}", font=("JetBrains Mono", 11),
                              text_color="#5a6a85", width=40).pack(side="left", padx=(12, 4))

                # Name
                ctk.CTkLabel(row, text=f"  {d.name}", font=("Segoe UI", 12),
                              text_color="#e2e8f0", anchor="w").pack(side="left", fill="x",
                                                                       expand=True)

                # File count
                ctk.CTkLabel(row, text=f"{d.file_count:,} fich.",
                              font=("JetBrains Mono", 10), text_color="#5a6a85",
                              width=90).pack(side="right", padx=(4, 12))

                # Size
                pct = (d.size / self.tree_data.size * 100) if self.tree_data.size > 0 else 0
                ctk.CTkLabel(row, text=f"{pct:.1f}%",
                              font=("JetBrains Mono", 10), text_color="#5a6a85",
                              width=50).pack(side="right", padx=4)

                ctk.CTkLabel(row, text=format_size(d.size),
                              font=("JetBrains Mono", 12, "bold"),
                              text_color=size_color(d.size),
                              width=100, anchor="e").pack(side="right", padx=4)

                # Bar
                bar_frame = ctk.CTkFrame(row, fg_color="#0a0e17", width=150, height=8,
                                           corner_radius=4)
                bar_frame.pack(side="right", padx=(8, 4))
                bar_frame.pack_propagate(False)

                bar_pct = d.size / max_size if max_size > 0 else 0
                bar_fill = ctk.CTkFrame(bar_frame, fg_color=size_color(d.size),
                                          corner_radius=4, height=8)
                bar_fill.place(relwidth=bar_pct, relheight=1.0)

    def _render_tree(self):
        """Peuple le treeview arborescence."""
        for item in self.folder_tree.get_children():
            self.folder_tree.delete(item)

        if not self.tree_data:
            return

        self._insert_tree_node("", self.tree_data, self.tree_data.size)

    def _insert_tree_node(self, parent_id: str, folder: FolderInfo, root_size: int):
        pct = (folder.size / root_size * 100) if root_size > 0 else 0
        node_id = self.folder_tree.insert(
            parent_id, "end",
            text=f"  {folder.name}",
            values=(format_size(folder.size), f"{folder.file_count:,}", f"{pct:.1f}%"),
            tags=(folder.path,)
        )

        for child in folder.children:
            self._insert_tree_node(node_id, child, root_size)

    def _render_files(self):
        """Peuple le tableau de fichiers."""
        for item in self.file_table.get_children():
            self.file_table.delete(item)

        for f in self.top_files:
            self.file_table.insert("", "end", values=(
                f.name,
                format_size(f.size),
                f.extension,
                f.modified,
                f.path
            ), tags=(str(f.size),))

    def _render_extensions(self):
        """Affiche les stats par extension."""
        parent = self.ext_frame
        for w in parent.winfo_children():
            w.destroy()

        if not self.ext_stats:
            return

        colors = [
            "#3b82f6", "#8b5cf6", "#06b6d4", "#10b981", "#f59e0b",
            "#ef4444", "#ec4899", "#6366f1", "#14b8a6", "#f97316",
            "#84cc16", "#a855f7", "#22d3ee", "#fb923c", "#e879f9"
        ]

        max_size = self.ext_stats[0]["size"] if self.ext_stats else 1

        # Grid layout - 3 columns
        row_frame = None
        for i, ext in enumerate(self.ext_stats):
            if i % 3 == 0:
                row_frame = ctk.CTkFrame(parent, fg_color="transparent")
                row_frame.pack(fill="x", padx=8, pady=2)

            color = colors[i % len(colors)]
            card = ctk.CTkFrame(row_frame, fg_color="#1a2235", corner_radius=8,
                                 border_width=1, border_color="#2a3550")
            card.pack(side="left", fill="x", expand=True, padx=4, pady=4)

            inner = ctk.CTkFrame(card, fg_color="transparent")
            inner.pack(fill="x", padx=14, pady=12)

            # Top row: rank + badge + size
            top = ctk.CTkFrame(inner, fg_color="transparent")
            top.pack(fill="x")

            ctk.CTkLabel(top, text=f"#{i+1}", font=("JetBrains Mono", 10),
                          text_color="#5a6a85").pack(side="left", padx=(0, 8))

            badge = ctk.CTkFrame(top, fg_color=color, corner_radius=4, height=24)
            badge.pack(side="left")
            badge.pack_propagate(False)
            ctk.CTkLabel(badge, text=f" {ext['ext']} ", font=("JetBrains Mono", 11, "bold"),
                          text_color="white").pack(padx=6, pady=2)

            ctk.CTkLabel(top, text=format_size(ext["size"]),
                          font=("JetBrains Mono", 14, "bold"),
                          text_color="#e2e8f0").pack(side="right")

            # Count
            ctk.CTkLabel(inner, text=f"{ext['count']:,} fichier{'s' if ext['count'] > 1 else ''}",
                          font=("Segoe UI", 11), text_color="#5a6a85",
                          anchor="w").pack(fill="x", pady=(4, 6))

            # Bar
            bar_bg = ctk.CTkFrame(inner, fg_color="#0a0e17", height=6, corner_radius=3)
            bar_bg.pack(fill="x")
            bar_bg.pack_propagate(False)
            bar_pct = ext["size"] / max_size if max_size > 0 else 0
            bar_fill = ctk.CTkFrame(bar_bg, fg_color=color, corner_radius=3, height=6)
            bar_fill.place(relwidth=bar_pct, relheight=1.0)

    # -------------------------------------------------------------------------
    # INTERACTIONS
    # -------------------------------------------------------------------------

    def _filter_tree(self, event=None):
        query = self.tree_search.get().strip().lower()
        if not query:
            # Re-render all
            self._render_tree()
            return
        # Simple: open all and let user visually find
        self._open_all_tree_nodes("")

    def _open_all_tree_nodes(self, parent):
        for item in self.folder_tree.get_children(parent):
            self.folder_tree.item(item, open=True)
            self._open_all_tree_nodes(item)

    def _filter_files(self, event=None):
        query = self.file_search.get().strip().lower()
        for item in self.file_table.get_children():
            self.file_table.delete(item)

        files = self.top_files
        if query:
            files = [f for f in self.top_files if
                     query in f.name.lower() or
                     query in f.extension.lower() or
                     query in f.path.lower()]

        for f in files:
            self.file_table.insert("", "end", values=(
                f.name, format_size(f.size), f.extension, f.modified, f.path
            ), tags=(str(f.size),))

    def _sort_files(self, col: str):
        if self.current_sort_col == col:
            self.current_sort_reverse = not self.current_sort_reverse
        else:
            self.current_sort_col = col
            self.current_sort_reverse = (col == "size")

        key_map = {
            "name": lambda f: f.name.lower(),
            "size": lambda f: f.size,
            "ext": lambda f: f.extension.lower(),
            "modified": lambda f: f.modified,
            "path": lambda f: f.path.lower(),
        }
        key_fn = key_map.get(col, lambda f: f.name.lower())
        self.top_files.sort(key=key_fn, reverse=self.current_sort_reverse)
        self._render_files()

    def _open_folder_in_explorer(self, event):
        sel = self.folder_tree.selection()
        if sel:
            tags = self.folder_tree.item(sel[0], "tags")
            if tags:
                path = tags[0]
                if os.path.isdir(path):
                    if platform.system() == "Windows":
                        os.startfile(path)
                    elif platform.system() == "Darwin":
                        os.system(f'open "{path}"')
                    else:
                        os.system(f'xdg-open "{path}"')

    def _open_file_location(self, event):
        sel = self.file_table.selection()
        if sel:
            values = self.file_table.item(sel[0], "values")
            if values and len(values) >= 5:
                filepath = values[4]
                folder = os.path.dirname(filepath)
                if os.path.isdir(folder):
                    if platform.system() == "Windows":
                        os.startfile(folder)
                    elif platform.system() == "Darwin":
                        os.system(f'open "{folder}"')
                    else:
                        os.system(f'xdg-open "{folder}"')

    def _export_html(self):
        """Exporte les resultats en fichier HTML."""
        if not self.tree_data:
            messagebox.showinfo("Info", "Aucune donnee a exporter.")
            return

        filepath = filedialog.asksaveasfilename(
            defaultextension=".html",
            filetypes=[("HTML", "*.html")],
            initialfile=f"DiskAnalyzer_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
        )
        if not filepath:
            return

        try:
            html = self._generate_html_report()
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(html)
            self.status_bar.update_text(f"Rapport exporte : {filepath}")
            webbrowser.open(filepath)
        except Exception as e:
            messagebox.showerror("Erreur", f"Impossible d'exporter : {e}")

    def _generate_html_report(self) -> str:
        """Genere un rapport HTML autonome (reutilise le template du script PS)."""

        def tree_to_dict(folder):
            if not folder:
                return None
            d = {
                "name": folder.name,
                "path": folder.path,
                "size": folder.size,
                "files": folder.file_count,
                "children": []
            }
            for child in folder.children:
                cd = tree_to_dict(child)
                if cd:
                    d["children"].append(cd)
            return d

        tree_json = json.dumps(tree_to_dict(self.tree_data), ensure_ascii=False)
        files_json = json.dumps([
            {"name": f.name, "path": f.path, "size": f.size,
             "ext": f.extension, "modified": f.modified}
            for f in self.top_files[:200]
        ], ensure_ascii=False)
        ext_json = json.dumps(self.ext_stats[:30], ensure_ascii=False)

        # Minimal HTML report
        return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>DiskAnalyzer Report</title>
<style>body{{font-family:sans-serif;background:#0a0e17;color:#e2e8f0;padding:20px}}
table{{border-collapse:collapse;width:100%}}th,td{{padding:8px 12px;text-align:left;
border-bottom:1px solid #2a3550}}th{{background:#111827;color:#8899b4;font-size:12px}}
tr:hover{{background:rgba(59,130,246,0.08)}}.mono{{font-family:monospace}}</style></head>
<body><h1>DiskAnalyzer - {platform.node()}</h1>
<p>Date: {datetime.now().strftime('%d/%m/%Y %H:%M')}</p>
<p>Espace analyse: {format_size(self.tree_data.size)} | Fichiers: {self.scanner.scanned_files:,}</p>
<h2>Top 200 fichiers</h2><table><thead><tr><th>#</th><th>Nom</th><th>Taille</th>
<th>Extension</th><th>Modifie</th><th>Chemin</th></tr></thead><tbody>
{''.join(f"<tr><td>{i+1}</td><td>{f.name}</td><td class='mono'>{format_size(f.size)}</td><td>{f.extension}</td><td>{f.modified}</td><td style='font-size:11px;color:#5a6a85'>{f.path}</td></tr>" for i, f in enumerate(self.top_files[:200]))}
</tbody></table>
<h2>Par extension</h2><table><thead><tr><th>#</th><th>Extension</th><th>Taille totale</th>
<th>Fichiers</th></tr></thead><tbody>
{''.join(f"<tr><td>{i+1}</td><td><b>{e['ext']}</b></td><td class='mono'>{format_size(e['size'])}</td><td>{e['count']:,}</td></tr>" for i, e in enumerate(self.ext_stats[:30]))}
</tbody></table>
<script>var treeData={tree_json};var topFiles={files_json};var extStats={ext_json};</script>
</body></html>"""

    # -------------------------------------------------------------------------
    # POLLING (thread-safe UI updates)
    # -------------------------------------------------------------------------

    def _start_poll(self):
        """Poll periodique pour mettre a jour la progression."""
        if self.scanner.scanning:
            self._on_scan_progress(
                self.scanner.scanned_dirs,
                self.scanner.scanned_files,
                self.scanner.total_size,
                self.scanner.current_dir
            )
        self.after(self._poll_interval, self._start_poll)


# =============================================================================
# ENTRY POINT
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description="DiskAnalyzer - Analyse d'espace disque")
    parser.add_argument("--path", "-p", default="", help="Chemin a analyser")
    parser.add_argument("--exclude", "-e", nargs="*", default=None,
                        help="Dossiers a exclure (ex: C:\\Windows)")
    parser.add_argument("--depth", "-d", type=int, default=5,
                        help="Profondeur max de scan (defaut: 5)")
    args = parser.parse_args()

    # Default exclude
    if args.exclude is None and platform.system() == "Windows":
        args.exclude = ["C:\\Windows"]

    app = DiskAnalyzerApp(
        initial_path=args.path,
        exclude_paths=args.exclude or [],
        max_depth=args.depth
    )
    app.mainloop()


if __name__ == "__main__":
    main()
