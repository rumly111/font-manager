/* Main.vala
 *
 * Copyright © 2009 - 2014 Jerry Casiano
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU General Public License as published by
 * the Free Software Foundation, either version 3 of the License, or
 * (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU General Public License for more details.
 *
 * You should have received a copy of the GNU General Public License
 * along with this program.  If not, see <http://www.gnu.org/licenses/>.
 *
 * Author:
 *  Jerry Casiano <JerryCasiano@gmail.com>
 */

namespace FontManager {

    [DBus (name = "org.gtk.FontManager")]
    public class Main: Gtk.Application  {

        public Components components;

        public Mode mode {
            get {
                return components.mode;
            }
            set {
                ((SimpleAction) lookup_action("mode")).set_state(value.to_string());
                components.mode = value;
            }
        }

        public Main (string app_id, ApplicationFlags app_flags) {
            Object(application_id : app_id, flags : app_flags);
        }

        protected override void activate () {
            string css_uri = "resource:///org/gnome/FontManager/FontManager.css";
            File css_file = File.new_for_uri(css_uri);
            Gtk.CssProvider provider = new Gtk.CssProvider();
            try {
                provider.load_from_file(css_file);
            } catch (Error e) {
                warning("Failed to load Css Provider! Application will not appear as expected.");
                warning(e.message);
            }
            Gtk.StyleContext.add_provider_for_screen(Gdk.Screen.get_default(), provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION);

            components = new Components();
            components.main = this;
            new MainWindow(components);
            add_window(components.main_window);
            components.loading = true;
            components.main_window.present();

            components.core = new Core();
            components.core.progress.connect((m, p, t) => {
                components.progress = ((float) p /(float) t);
                ensure_ui_update();
                }
            );
            components.core.init();
            components.model = new Model(components.core);
            components.set_reject(components.core.fontconfig.reject);
            components.set_all_models();
            components.loading = false;
            /* XXX : Workaround timing issue? wrong filter shown at startup */
            if (components.sidebar.standard.mode == MainSideBarMode.COLLECTION) {
                components.sidebar.standard.mode = MainSideBarMode.CATEGORY;
                components.sidebar.standard.mode = MainSideBarMode.COLLECTION;
            }

            components.main_window.delete_event.connect((w, e) => {
                on_quit();
                return true;
                }
            );

            components.core.fontconfig.changed.connect((f, e) => {
                components.queue_reload();
            });
            return;
        }

        public void on_quit () {
            components.main_window.hide();
            remove_window(components.main_window);
            quit();
        }

        public void on_about () {
            show_about_dialog(components.main_window);
            return;
        }

        public void on_help () {
            Gtk.show_uri(null, "help:%s".printf(NAME), Gdk.CURRENT_TIME);
            return;
        }

        public static int main (string [] args) {
            FontConfig.enable_user_config(false);
            Environment.set_application_name(About.NAME);
            Intl.bindtextdomain(NAME, null);
            Intl.bind_textdomain_codeset(NAME, null);
            Intl.textdomain(NAME);
            Intl.setlocale(LocaleCategory.ALL, null);
            Gtk.init(ref args);
            if (Migration.required()) {
                if (Migration.approved(null)) {
                    Migration.run();
                } else {
                    return 0;
                }
            }
            var main = new Main(BUS_ID, (ApplicationFlags.FLAGS_NONE));
            int res = main.run(args);
            return res;
        }

    }

}