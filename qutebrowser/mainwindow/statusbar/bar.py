# vim: ft=python fileencoding=utf-8 sts=4 sw=4 et:

# Copyright 2014-2017 Florian Bruhin (The Compiler) <mail@qutebrowser.org>
#
# This file is part of qutebrowser.
#
# qutebrowser is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# qutebrowser is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with qutebrowser.  If not, see <http://www.gnu.org/licenses/>.

"""The main statusbar widget."""

from PyQt5.QtCore import pyqtSignal, pyqtSlot, pyqtProperty, Qt, QSize, QTimer
from PyQt5.QtWidgets import QWidget, QHBoxLayout, QStackedLayout, QSizePolicy

from qutebrowser.browser import browsertab
from qutebrowser.config import config, style
from qutebrowser.utils import usertypes, log, objreg, utils
from qutebrowser.mainwindow.statusbar import (command, progress, keystring,
                                              percentage, url, tabindex)
from qutebrowser.mainwindow.statusbar import text as textwidget


class ColorFlags:

    """Flags which change the appearance of the statusbar.

    Attributes:
        prompt: If we're currently in prompt-mode.
        insert: If we're currently in insert mode.
        command: If we're currently in command mode.
        mode: The current caret mode (CaretMode.off/.on/.selection).
        private: Whether this window is in private browsing mode.
    """

    CaretMode = usertypes.enum('CaretMode', ['off', 'on', 'selection'])

    def __init__(self):
        self.prompt = False
        self.insert = False
        self.command = False
        self.caret = self.CaretMode.off
        self.private = False

    def to_stringlist(self):
        """Get a string list of set flags used in the stylesheet.

        This also combines flags in ways they're used in the sheet.
        """
        strings = []
        if self.prompt:
            strings.append('prompt')
        if self.insert:
            strings.append('insert')
        if self.command:
            strings.append('command')
        if self.private:
            strings.append('private')

        if self.private and self.command:
            strings.append('private-command')

        if self.caret == self.CaretMode.on:
            strings.append('caret')
        elif self.caret == self.CaretMode.selection:
            strings.append('caret-selection')
        else:
            assert self.caret == self.CaretMode.off

        return strings


class StatusBar(QWidget):

    """The statusbar at the bottom of the mainwindow.

    Attributes:
        txt: The Text widget in the statusbar.
        keystring: The KeyString widget in the statusbar.
        percentage: The Percentage widget in the statusbar.
        url: The UrlText widget in the statusbar.
        prog: The Progress widget in the statusbar.
        cmd: The Command widget in the statusbar.
        _hbox: The main QHBoxLayout.
        _stack: The QStackedLayout with cmd/txt widgets.
        _win_id: The window ID the statusbar is associated with.
        _page_fullscreen: Whether the webpage (e.g. a video) is shown
                          fullscreen.

    Signals:
        resized: Emitted when the statusbar has resized, so the completion
                 widget can adjust its size to it.
                 arg: The new size.
        moved: Emitted when the statusbar has moved, so the completion widget
               can move to the right position.
               arg: The new position.
    """

    resized = pyqtSignal('QRect')
    moved = pyqtSignal('QPoint')
    _severity = None
    _color_flags = []

    STYLESHEET = """

        QWidget#StatusBar,
        QWidget#StatusBar QLabel,
        QWidget#StatusBar QLineEdit {
            font: {{ font['statusbar'] }};
            background-color: {{ color['statusbar.bg'] }};
            color: {{ color['statusbar.fg'] }};
        }

        QWidget#StatusBar[color_flags~="private"],
        QWidget#StatusBar[color_flags~="private"] QLabel,
        QWidget#StatusBar[color_flags~="private"] QLineEdit {
            color: {{ color['statusbar.fg.private'] }};
            background-color: {{ color['statusbar.bg.private'] }};
        }

        QWidget#StatusBar[color_flags~="caret"],
        QWidget#StatusBar[color_flags~="caret"] QLabel,
        QWidget#StatusBar[color_flags~="caret"] QLineEdit {
            color: {{ color['statusbar.fg.caret'] }};
            background-color: {{ color['statusbar.bg.caret'] }};
        }

        QWidget#StatusBar[color_flags~="caret-selection"],
        QWidget#StatusBar[color_flags~="caret-selection"] QLabel,
        QWidget#StatusBar[color_flags~="caret-selection"] QLineEdit {
            color: {{ color['statusbar.fg.caret-selection'] }};
            background-color: {{ color['statusbar.bg.caret-selection'] }};
        }

        QWidget#StatusBar[color_flags~="prompt"],
        QWidget#StatusBar[color_flags~="prompt"] QLabel,
        QWidget#StatusBar[color_flags~="prompt"] QLineEdit {
            color: {{ color['prompts.fg'] }};
            background-color: {{ color['prompts.bg'] }};
        }

        QWidget#StatusBar[color_flags~="insert"],
        QWidget#StatusBar[color_flags~="insert"] QLabel,
        QWidget#StatusBar[color_flags~="insert"] QLineEdit {
            color: {{ color['statusbar.fg.insert'] }};
            background-color: {{ color['statusbar.bg.insert'] }};
        }

        QWidget#StatusBar[color_flags~="command"],
        QWidget#StatusBar[color_flags~="command"] QLabel,
        QWidget#StatusBar[color_flags~="command"] QLineEdit {
            color: {{ color['statusbar.fg.command'] }};
            background-color: {{ color['statusbar.bg.command'] }};
        }

        QWidget#StatusBar[color_flags~="private-command"],
        QWidget#StatusBar[color_flags~="private-command"] QLabel,
        QWidget#StatusBar[color_flags~="private-command"] QLineEdit {
            color: {{ color['statusbar.fg.command.private'] }};
            background-color: {{ color['statusbar.bg.command.private'] }};
        }
    """

    def __init__(self, *, win_id, private, parent=None):
        super().__init__(parent)
        objreg.register('statusbar', self, scope='window', window=win_id)
        self.setObjectName(self.__class__.__name__)
        self.setAttribute(Qt.WA_StyledBackground)
        style.set_register_stylesheet(self)

        self.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Fixed)

        self._win_id = win_id
        self._option = None
        self._page_fullscreen = False
        self._color_flags = ColorFlags()
        self._color_flags.private = private

        self._hbox = QHBoxLayout(self)
        self.set_hbox_padding()
        objreg.get('config').changed.connect(self.set_hbox_padding)
        self._hbox.setSpacing(5)

        self._stack = QStackedLayout()
        self._hbox.addLayout(self._stack)
        self._stack.setContentsMargins(0, 0, 0, 0)

        self.cmd = command.Command(private=private, win_id=win_id)
        self._stack.addWidget(self.cmd)
        objreg.register('status-command', self.cmd, scope='window',
                        window=win_id)

        self.txt = textwidget.Text()
        self._stack.addWidget(self.txt)

        self.cmd.show_cmd.connect(self._show_cmd_widget)
        self.cmd.hide_cmd.connect(self._hide_cmd_widget)
        self._hide_cmd_widget()

        self.keystring = keystring.KeyString()
        self._hbox.addWidget(self.keystring)

        self.url = url.UrlText()
        self._hbox.addWidget(self.url)

        self.percentage = percentage.Percentage()
        self._hbox.addWidget(self.percentage)

        self.tabindex = tabindex.TabIndex()
        self._hbox.addWidget(self.tabindex)

        # We add a parent to Progress here because it calls self.show() based
        # on some signals, and if that happens before it's added to the layout,
        # it will quickly blink up as independent window.
        self.prog = progress.Progress(self)
        self._hbox.addWidget(self.prog)

        objreg.get('config').changed.connect(self.maybe_hide)
        QTimer.singleShot(0, self.maybe_hide)

    def __repr__(self):
        return utils.get_repr(self)

    @config.change_filter('ui', 'hide-statusbar')
    def maybe_hide(self):
        """Hide the statusbar if it's configured to do so."""
        hide = config.get('ui', 'hide-statusbar')
        if hide or self._page_fullscreen:
            self.hide()
        else:
            self.show()

    @config.change_filter('ui', 'statusbar-padding')
    def set_hbox_padding(self):
        padding = config.get('ui', 'statusbar-padding')
        self._hbox.setContentsMargins(padding.left, 0, padding.right, 0)

    @pyqtProperty(list)
    def color_flags(self):
        """Getter for self.color_flags, so it can be used as Qt property."""
        return self._color_flags.to_stringlist()

    def set_mode_active(self, mode, val):
        """Setter for self.{insert,command,caret}_active.

        Re-set the stylesheet after setting the value, so everything gets
        updated by Qt properly.
        """
        if mode == usertypes.KeyMode.insert:
            log.statusbar.debug("Setting insert flag to {}".format(val))
            self._color_flags.insert = val
        if mode == usertypes.KeyMode.command:
            log.statusbar.debug("Setting command flag to {}".format(val))
            self._color_flags.command = val
        elif mode in [usertypes.KeyMode.prompt, usertypes.KeyMode.yesno]:
            log.statusbar.debug("Setting prompt flag to {}".format(val))
            self._color_flags.prompt = val
        elif mode == usertypes.KeyMode.caret:
            tab = objreg.get('tabbed-browser', scope='window',
                             window=self._win_id).currentWidget()
            log.statusbar.debug("Setting caret flag - val {}, selection "
                                "{}".format(val, tab.caret.selection_enabled))
            if val:
                if tab.caret.selection_enabled:
                    self._set_mode_text("{} selection".format(mode.name))
                    self._color_flags.caret = ColorFlags.CaretMode.selection
                else:
                    self._set_mode_text(mode.name)
                    self._color_flags.caret = ColorFlags.CaretMode.on
            else:
                self._color_flags.caret = ColorFlags.CaretMode.off
        self.setStyleSheet(style.get_stylesheet(self.STYLESHEET))

    def _set_mode_text(self, mode):
        """Set the mode text."""
        text = "-- {} MODE --".format(mode.upper())
        self.txt.set_text(self.txt.Text.normal, text)

    def _show_cmd_widget(self):
        """Show command widget instead of temporary text."""
        self._stack.setCurrentWidget(self.cmd)
        self.show()

    def _hide_cmd_widget(self):
        """Show temporary text instead of command widget."""
        log.statusbar.debug("Hiding cmd widget")
        self._stack.setCurrentWidget(self.txt)
        self.maybe_hide()

    @pyqtSlot(str)
    def set_text(self, val):
        """Set a normal (persistent) text in the status bar."""
        self.txt.set_text(self.txt.Text.normal, val)

    @pyqtSlot(usertypes.KeyMode)
    def on_mode_entered(self, mode):
        """Mark certain modes in the commandline."""
        keyparsers = objreg.get('keyparsers', scope='window',
                                window=self._win_id)
        if keyparsers[mode].passthrough:
            self._set_mode_text(mode.name)
        if mode in [usertypes.KeyMode.insert,
                    usertypes.KeyMode.command,
                    usertypes.KeyMode.caret,
                    usertypes.KeyMode.prompt,
                    usertypes.KeyMode.yesno]:
            self.set_mode_active(mode, True)

    @pyqtSlot(usertypes.KeyMode, usertypes.KeyMode)
    def on_mode_left(self, old_mode, new_mode):
        """Clear marked mode."""
        keyparsers = objreg.get('keyparsers', scope='window',
                                window=self._win_id)
        if keyparsers[old_mode].passthrough:
            if keyparsers[new_mode].passthrough:
                self._set_mode_text(new_mode.name)
            else:
                self.txt.set_text(self.txt.Text.normal, '')
        if old_mode in [usertypes.KeyMode.insert,
                        usertypes.KeyMode.command,
                        usertypes.KeyMode.caret,
                        usertypes.KeyMode.prompt,
                        usertypes.KeyMode.yesno]:
            self.set_mode_active(old_mode, False)

    @pyqtSlot(bool)
    def on_page_fullscreen_requested(self, on):
        self._page_fullscreen = on
        self.maybe_hide()

    @pyqtSlot(browsertab.AbstractTab)
    def on_tab_changed(self, tab):
        """Notify sub-widgets when the tab has been changed."""
        self.url.on_tab_changed(tab)
        self.prog.on_tab_changed(tab)
        self.percentage.on_tab_changed(tab)
        assert tab.private == self._color_flags.private

    def resizeEvent(self, e):
        """Extend resizeEvent of QWidget to emit a resized signal afterwards.

        Args:
            e: The QResizeEvent.
        """
        super().resizeEvent(e)
        self.resized.emit(self.geometry())

    def moveEvent(self, e):
        """Extend moveEvent of QWidget to emit a moved signal afterwards.

        Args:
            e: The QMoveEvent.
        """
        super().moveEvent(e)
        self.moved.emit(e.pos())

    def minimumSizeHint(self):
        """Set the minimum height to the text height plus some padding."""
        padding = config.get('ui', 'statusbar-padding')
        width = super().minimumSizeHint().width()
        height = self.fontMetrics().height() + padding.top + padding.bottom
        return QSize(width, height)
