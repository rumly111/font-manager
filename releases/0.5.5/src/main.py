"""
Font Manager, a font management application for the GNOME desktop
"""
# Font Manager, a font management application for the GNOME desktop
#
# Copyright (C) 2009, 2010 Jerry Casiano
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to:
#
#    Free Software Foundation, Inc.
#    51 Franklin Street, Fifth Floor
#    Boston, MA 02110-1301, USA.

# Disable warnings related to gettext
# pylint: disable-msg=E0602
# Disable warnings related to missing docstrings, for now...
# pylint: disable-msg=C0111

import os
import sys
import gtk
import gobject
import logging
import UserDict
import shutil
import subprocess

from os.path import exists, join, splitext

import core
import _fontutils

from ui import fontconfig
from constants import PACKAGE, PACKAGE_DATA_DIR, TMP_DIR
from ui.export import Export
from ui.library import InstallFonts, RemoveFonts
from ui.treeviews import Treeviews
from ui.previews import Browse, Previews
from utils.common import open_folder, delete_cache, delete_database, \
            reset_fontconfig_cache, fc_config_load_user_dirs, fc_config_reload
from utils.xmlutils import save_collections


class Main(object):
    """
    Where everything starts.
    """
    def __init__(self):
        # If this exists it means something probably went wrong last run,
        # for now just get rid of it so we don't have even more issues
        if exists(TMP_DIR):
            shutil.rmtree(TMP_DIR, ignore_errors=True)
        logging.info("Font Manager is now starting")
        # http://code.google.com/p/font-manager/issues/detail?id=24
        # Be friendlier to users with huge? collections by only affecting
        # our internal config instead of the system one.
        _fontutils.FcEnableHomeConfig(False)
        fc_config_load_user_dirs()
        self.objects = ObjectContainer()
        self.main_window = self.objects['MainWindow']
        self.main_window.connect('window-state-event', self._set_window_state)
        self.preferences = self.objects['Preferences']
        self.menus = self.objects['PopupMenus']
        # Showtime
        self.objects.load_widgets()
        if self.preferences.hpane > 0:
            pane = self.objects['HorizontalPane']
            pane.set_position(self.preferences.hpane)
        if self.preferences.vpane > 0:
            pane = self.objects['VerticalPane']
            pane.set_position(self.preferences.vpane)
        if self.preferences.windowsize != '0:0':
            width, height = self.preferences.windowsize.split(':')
            self.main_window.resize(int(width), int(height))
        if self.preferences.maximized:
            self.main_window.maximize()
        self.main_window.show()
        if self.preferences.minimizeonstart:
            self.main_window.iconify()
        # This is called just to force an update
        while gtk.events_pending():
            gtk.main_iteration()
        # Keep a handle to this connection, so we can change it if requested
        self.quit_id = self.main_window.connect('delete-event', self.quit)
        # Load
        self.objects.load_core()
        # Notification
        if self.preferences.minimizeonstart:
            self.objects.notify_send(_('Finished loading %s families') % \
                                self.objects['FontManager'].total_families())
        logging.info(_('Finished loading %s families') % \
                                self.objects['FontManager'].total_families())
        # Main UI elements
        self.objects.load_ui_elements()
        self.previews = self.objects['Previews']
        self.treeviews = self.objects['Treeviews']
        if self.preferences.browsemode:
            self.objects['Browse'] = Browse(self.objects)
            self.objects['BrowseFonts'].show()
        self.objects.connect_callbacks()
        self._connect_callbacks()
        # Tray icon
        self.tray_icon = None
        if self.preferences.minimizeonclose:
            self._use_tray_icon(None)
        # Install/Remove/Export functions
        self.export = None
        self.installer = None
        self.remover = None
        # We disabled user configuration files at the start.
        # This means any user settings just got dropped so reload them
        fc_config_reload()
        # This also happens whenever these signals are emitted...
        self.main_window.connect_after('style-set', fc_config_reload)
        self.main_window.connect_after('direction-changed', fc_config_reload)
        # What the hell, let's put Main into the container... :-D
        self.objects['Main'] = self

    def _connect_callbacks(self):
        # Menu callbacks
        self.menus['ManageFolder'].connect('activate', self._on_open_folder)
        self.menus['ManageInstall'].connect('activate', self._on_install_fonts)
        self.menus['ManageRemove'].connect('activate', self._on_remove_fonts)
        self.menus['TrayInstall'].connect('activate', self._on_install_fonts)
        self.menus['TrayRemove'].connect('activate', self._on_remove_fonts)
        self.menus['TrayFontPreferences'].connect('activate', _on_font_settings)
        self.menus['TrayCharacterMap'].connect('activate',
                                                    self.previews.on_char_map)
        self.menus['TrayPreferences'].connect('activate', self.objects.on_prefs)
        if 'yelp' in self.objects['AvailableApps']:
            self.menus['TrayHelp'].connect('activate', _on_help)
        else:
            self.menus['TrayHelp'].set_sensitive(False)
            self.menus['TrayHelp'].set_tooltip_text\
            (_('Yelp is required to view help files'))
        self.menus['TrayAbout'].connect('activate', _on_about_dialog,
                                            self.objects['AboutDialog'])
        self.menus['TrayQuit'].connect('activate', self.quit)
        if 'gnome-appearance-properties' in self.objects['AvailableApps']:
            self.menus['DesktopSettings'].connect('activate', _on_font_settings)
        else:
            self.menus['DesktopSettings'].set_sensitive(False)
            self.menus['DesktopSettings'].set_tooltip_text\
            (_('This option requires gnome-appearance-properties'))
        self.menus['FontConfigSettings'].connect('activate', 
                                        _on_fontconfig_settings, self.objects)
        self.menus['AliasEdit'].connect('activate', 
                                        _on_edit_aliases, self.objects)
        # Miscellaneous
        self.objects['Refresh'].connect('clicked', self.reload, True)
        self.objects['Manage'].connect('button-press-event',
                                        self.menus.show_manage_menu)
        self.objects['FontSettings'].connect('button-press-event',
                                        self.menus.show_settings_menu)
        self.objects['Export'].connect('clicked', self._on_export)
        self.preferences.connect('update-tray-icon', self._use_tray_icon)
        return

    def _on_export(self, unused_widget):
        if not self.export:
            self.export = Export(self.objects)
        self.export.run()
        return

    def _on_install_fonts(self, unused_widget):
        if not self.installer:
            self.installer = InstallFonts(self.objects)
        self.installer.run()
        return

    def _on_open_folder(self, unused_widget):
        open_folder(self.objects['Preferences'].folder, self.objects)
        return

    def _on_remove_fonts(self, unused_widget):
        if not self.remover:
            self.remover = RemoveFonts(self.objects)
        self.remover.run()
        return

    def _on_tray_icon_clicked(self, unused_widget):
        """
        Show or hide application when tray icon is clicked
        """
        if not self.main_window.get_property('visible'):
            self.main_window.set_skip_taskbar_hint(False)
            self.main_window.present()
        else:
            self.main_window.set_skip_taskbar_hint(True)
            self.main_window.hide()
        return

    def reload(self, unused_widget, del_cache = False):
        self._save_settings()
        self.objects.notify_send(_('Font Manager will restart in a moment'))
        logging.info("Restarting...")
        if del_cache:
            delete_cache()
            delete_database()
            reset_fontconfig_cache()
        try:
            font_manager = join('/usr/local', 'bin/font-manager')
            os.execvp(font_manager,
                        ('--execvp_needs_to_stop_crying="True"',))
        except OSError, error:
            logging.error(error)
        return

    def _save_settings(self):
        if not self.preferences.maximized:
            width, height = self.main_window.get_size()
            self.preferences.windowsize = '%s:%s' % (width, height)
        logging.info("Saving configuration")
        self.preferences.save()
        save_collections(self.objects)
        return

    def _set_window_state(self, widget, event):
        if event.type == gtk.gdk.WINDOW_STATE:
            if event.new_window_state == gtk.gdk.WINDOW_STATE_MAXIMIZED:
                self.preferences.maximized = True
            else:
                self.preferences.maximized = False
        return

    def _use_tray_icon(self, unused_cls_instance, minimize = True):
        if not self.tray_icon:
            self.tray_icon = \
            gtk.status_icon_new_from_icon_name('preferences-desktop-font')
            self.tray_icon.set_tooltip(_('Font Manager'))
            self.tray_icon.connect('activate', self._on_tray_icon_clicked)
            self.tray_icon.connect('popup-menu', self.menus.show_tray_menu)
        self.tray_icon.set_visible(minimize)
        self.main_window.disconnect(self.quit_id)
        if minimize:
            self.quit_id = \
            self.main_window.connect('delete-event', _delete_handler)
        else:
            self.quit_id = \
            self.main_window.connect('delete-event', self.quit)
            if not self.main_window.get_property('visible'):
                self.main_window.present()
                self.main_window.set_skip_taskbar_hint(False)
                while gtk.events_pending():
                    gtk.main_iteration()
        return

    def quit(self, unused_widget = None, possible_event = None):
        self._save_settings()
        logging.info("Exiting...")
        gtk.main_quit()
        # FIXME
        # This shouldn't be needed...
        sys.exit(0)


class ObjectContainer(UserDict.UserDict):
    """
    Provide a convenient way to share objects between classes.
    """
    _widgets = (
        'TopBox', 'MainBox', 'StatusBox', 'HorizontalPane',
        'CategoryTree', 'CollectionTree', 'NewCollection', 'RemoveCollection',
        'EnableCollection', 'DisableCollection', 'VerticalPane', 'FamilyScroll',
        'FamilyTree', 'FamilySearchBox', 'EnableFamily', 'DisableFamily',
        'RemoveFamily', 'ColorSelect', 'FontInformation', 'CharacterMap',
        'CompareButtonsBox', 'AddToCompare', 'RemoveFromCompare',
        'ClearComparison', 'PreviewScroll', 'CompareScroll', 'Export',
        'ClearComparison', 'FontSizeSpinButton', 'FontPreview', 'CompareTree',
        'FontSizeSlider', 'CustomTextEntry', 'SearchFonts', 'CompareFonts',
        'CustomText', 'About', 'Help', 'AppPreferences', 'FontSettings',
        'Manage', 'FamilyTotal', 'ProgressBar', 'LoadingLabel', 'ProgressLabel',
        'Throbber', 'Refresh', 'SizeAdjustment', 'FontOptionsBox',
        'MadFontsWarning', 'ColorSelector', 'CloseColorSelector',
        'ForegroundColor', 'BackgroundColor', 'AboutDialog', 'AppOptionsBox',
        'FontInstallDialog', 'DuplicatesWarning', 'DuplicatesView',
        'FileMissingDialog', 'FileMissingView', 'FontRemovalDialog',
        'RemoveFontsTree', 'RemoveSearchEntry', 'RemoveFontsButton',
        'ExportDialog', 'ExportAsArchive', 'ExportAsPDF', 'ExportTo',
        'ExportFileChooserBox', 'ExportFileChooser', 'ExportPermissionsWarning',
        'ExportArchiveOptionsBox', 'IncludeSampler', 'SearchBox', 'Advanced',
        'BrowseFonts', 'MainNotebook', 'BackButton', 'BrowseTree',
        'BrowseScroll', 'CollectionButtonsFrame', 'SidePaneScroll'
        )
    def __init__(self):
        UserDict.UserDict.__init__(self)
        self.data = {}
        self.prefs_dialog = None
        self.builder = gtk.Builder()
        self.builder.set_translation_domain('font-manager')
        self.builder.add_from_file(join(PACKAGE_DATA_DIR, 'font-manager.ui'))
        self.data['MainWindow'] = self.builder.get_object('MainWindow')
        self.data['Preferences'] = core.get_preferences()
        # Preferences dialog restarts before it's done if this is enabled
        #self.data['Preferences'].connect('update-font-dirs', self.reload)
        self.data['AvailableApps'] = core.get_applist()
        self.data['PopupMenus'] = PopupMenu()
        self.data['Browse'] = None
        # Find and set up icon for use throughout app windows
        try:
            icon_theme = gtk.icon_theme_get_default()
            self.icon = icon_theme.load_icon("preferences-desktop-font", 48, 0)
            gtk.window_set_default_icon_list(self.icon)
        except gobject.GError, exc:
            logging.warn("Could not find preferences-desktop-font icon", exc)
            self.icon = None
        # Show notifications, if available
        self.message = None
        try:
            import pynotify
            if pynotify.init('font-manager'):
                self.message = pynotify.Notification
        except ImportError:
            pass

    def connect_callbacks(self):
        """
        Connect callbacks to local functions.
        """
        self.data['About'].connect('clicked', _on_about_dialog,
                                            self.data['AboutDialog'])
        if 'yelp' in self.data['AvailableApps']:
            self.data['Help'].connect('clicked', _on_help)
        else:
            self.data['Help'].set_sensitive(False)
            self.data['Help'].set_tooltip_text\
            (_('Yelp is required to view help files'))
        self.data['AppPreferences'].connect('clicked', self.on_prefs)
        return

    def load_core(self):
        """
        Load FontManager.
        """
        self.set_sensitive(False)
        core.PROGRESS_CALLBACK = self.progress_callback
        self.data['FontManager'] = core.get_manager(self.data['MainWindow'])
        self.set_sensitive(True)
        return

    def load_ui_elements(self):
        self.data['Previews'] = Previews(self)
        self.data['Treeviews'] = Treeviews(self)
        return

    def load_widgets(self):
        """
        Load widgets from .ui file.

        Setup any widgets that are not defined in the .ui file.
        """
        for widget in self._widgets:
            self.data[widget] = self.builder.get_object(widget)
        # Undefined widgets
        self.data['StyleCombo'] =  gtk.combo_box_new_text()
        self.data['StyleCombo'].set_focus_on_click(False)
        self.data['FontOptionsBox'].pack_end(self.data['StyleCombo'],
                                                            False, False)
        self.data['StyleCombo'].show()
        return

    def notify_send(self, message):
        """
        Display a notification bubble.
        """
        if self.message is None:
            return
        notification = self.message(_('Font Manager'), '%s' % message)
        if self.icon:
            notification.set_icon_from_pixbuf(self.icon)
        notification.show()
        while gtk.events_pending():
            gtk.main_iteration()
        return

    def on_prefs(self, unused_widget):
        """
        Display preferences dialog.
        """
        from ui.preferences import PreferencesDialog
        if self.prefs_dialog is None:
            self.prefs_dialog = PreferencesDialog(self)
            self.prefs_dialog.run(self)
        else:
            self.prefs_dialog.run(self)
        return

    def progress_callback(self, family, total, processed):
        """
        Set progressbar text and percentage.
        """
        if family is not None:
            self.data['ProgressBar'].set_text(family)
        if processed > 0 and processed <= total:
            self.data['ProgressBar'].set_fraction(float(processed)/float(total))
        while gtk.events_pending():
            gtk.main_iteration()
        return

    def reload(self, *args):
        self.data['Main'].reload(None)
        return

    def set_sensitive(self, state = True):
        self.data['MainBox'].set_sensitive(state)
        self.data['Refresh'].set_sensitive(state)
        for widget in self.data['AppOptionsBox'].get_children():
            widget.set_sensitive(state)
        if state:
            self.data['ProgressBar'].hide()
            self.update_family_total()
        else:
            self.data['FamilyTotal'].set_text('')
            self.data['ProgressBar'].set_text('')
            self.data['ProgressBar'].show()
        while gtk.events_pending():
            gtk.main_iteration()
        return

    def update_family_total(self):
        """
        Update family total displayed in "statusbar".
        """
        self.data['FamilyTotal'].set_text\
        (_('Families : %s') % str(self.data['FontManager'].total_families()))
        return


class PopupMenu(UserDict.UserDict):
    _widgets = (
        'TrayMenu', 'TrayInstall', 'TrayRemove', 'TrayFontPreferences',
        'TrayCharacterMap', 'TrayPreferences', 'TrayHelp', 'TrayAbout',
        'TrayQuit', 'ManageMenu', 'ManageInstall', 'ManageRemove',
        'ManageFolder', 'SettingsMenu', 'DesktopSettings', 'FontConfigSettings',
        'AliasEdit'
        )
    def __init__(self, objects = None):
        UserDict.UserDict.__init__(self)
        self.data = {}
        if objects is None:
            self.builder = gtk.Builder()
        else:
            self.builder = objects.builder
        self.builder.add_from_file(join(PACKAGE_DATA_DIR, 'menus.ui'))
        for widget in self._widgets:
            self.data[widget] = self.builder.get_object(widget)
        self.tray_menu = self.data['TrayMenu']
        self.manage_menu = self.data['ManageMenu']
        self.settings_menu = self.data['SettingsMenu']

    def show_manage_menu(self, unused_widget, event):
        """
        Display "manage fonts' menu.
        """
        if event.button != 1:
            return
        self.manage_menu.popup(None, None, None, event.button, event.time)
        return

    def show_tray_menu(self, unused_widget, button, event_time):
        """
        Display tray menu.
        """
        self.tray_menu.popup(None, None, None, button, event_time)
        return

    def show_settings_menu(self, unused_widget, event):
        """
        Display settings menu.
        """
        if event.button != 1:
            return
        self.settings_menu.popup(None, None, None, event.button, event.time)
        return


def _delete_handler(window, unused_event):
    """
    PyGTK destroys the window by default, returning True from this function
    tells PyGTK that no further action is needed.

    Returning False would tell PyGTK to perform these actions then go ahead
    and destroy the window.
    """
    window.set_skip_taskbar_hint(True)
    window.hide()
    return True

def _on_about_dialog(unused_widget, dialog):
    """
    Launch about dialog.
    """
    dialog.run()
    dialog.hide()
    return

def _on_edit_aliases(unused_widget, objects):
    fontconfig.AliasEdit(objects)
    return

def _on_font_settings(unused_widget):
    """
    Launch gnome-appearance-properties with the fonts tab active.
    """
    try:
        logging.info("Launching font preferences dialog")
        subprocess.Popen(['gnome-appearance-properties', '--show-page=fonts'])
    except OSError, error:
        logging.error("Error: %s" % error)
    return

def _on_fontconfig_settings(unused_widget, objects):
    """
    Display a dialog so user can tweak fontconfig settings for individual fonts.
    """
    fontconfig.ConfigEdit(objects)
    return

def _on_help(unused_widget):
    """
    Open help pages in browser.
    """
    # The trailing slash is required, otherwise yelp will not find images...
    lang = splitext(os.getenv('LANG', 'en_US'))[0] + '/'
    help_dir = join('/usr/local', 'share', PACKAGE, 'help')
    help_files = join(help_dir, lang)
    if not exists(help_files):
        logging.info('No translation exists for %s, using default...' % \
                                        splitext(os.environ['LANG'])[0])
        help_files = join(help_dir, 'C/')
    try:
        subprocess.Popen(['yelp', help_files])
        logging.info("Launching Help browser")
    except OSError:
        logging.warn("Could not find any suitable browser")
    return

