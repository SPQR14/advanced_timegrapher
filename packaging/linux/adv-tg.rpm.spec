Name:           adv-tg
Version:        %{version}
# No dist tag: one package for every RPM distribution
Release:        1
Summary:        Horological acoustic timing and rate measurement machine
License:        GPL-3.0-or-later
URL:            https://github.com/SPQR14/advanced_timegrapher
Group:          Applications/Engineering

# Disable automatic debuginfo packaging (PyInstaller bundle already contains stripped binaries)
%define debug_package %{nil}
%define __strip /bin/true
# No /usr/lib/.build-id links: the bundled libraries share build ids with the system's
# packages (libpng, libbrotli...) and the links would conflict with them on install
%define _build_id_links none

# Disallow automatic internal library dependencies generation for bundled PyInstaller dependencies
AutoReqProv:    no
%define _use_internal_dependency_generator 0
%global __find_provides %{nil}
%global __find_requires %{nil}

# The bundle carries Python, Qt, numpy, PortAudio and libsndfile. What it takes from the system
# is declared by library name, which resolves on Fedora, RHEL/Alma/Rocky and openSUSE alike
# (package names differ between them). Built on EL9: glibc >= 2.34.
Requires:       libc.so.6(GLIBC_2.34)(64bit)
Requires:       libstdc++.so.6()(64bit)
Requires:       libgcc_s.so.1()(64bit)
Requires:       libasound.so.2()(64bit)
Requires:       libglib-2.0.so.0()(64bit)
Requires:       libgobject-2.0.so.0()(64bit)
Requires:       libgio-2.0.so.0()(64bit)
Requires:       libfontconfig.so.1()(64bit)
Requires:       libfreetype.so.6()(64bit)
Requires:       libX11.so.6()(64bit)
Requires:       libX11-xcb.so.1()(64bit)
Requires:       libxcb.so.1()(64bit)
Requires:       libGL.so.1()(64bit)
Requires:       libEGL.so.1()(64bit)
Requires:       libwayland-client.so.0()(64bit)
Requires:       libwayland-cursor.so.0()(64bit)
Requires:       libwayland-egl.so.1()(64bit)

%description
Advanced Timegrapher is an open-source watch timing machine and horological acoustic
analysis application written in Python and PyQt. It performs real-time rate, beat
error, and amplitude measurements of mechanical watch escapements without requiring
specialized proprietary hardware.

%prep
# No source unpacking needed: using pre-built PyInstaller application bundle

%build
# Pre-built via PyInstaller (dist/adv-tg)

%install
rm -rf %{buildroot}
mkdir -p %{buildroot}/opt/adv-tg
mkdir -p %{buildroot}/usr/bin
mkdir -p %{buildroot}/usr/share/applications
mkdir -p %{buildroot}/usr/share/icons/hicolor/scalable/apps
mkdir -p %{buildroot}/usr/share/pixmaps

# Copy standalone PyInstaller application bundle
cp -a %{_sourcedir}/dist/adv-tg/* %{buildroot}/opt/adv-tg/
chmod 755 %{buildroot}/opt/adv-tg/adv-tg

# Create symlink in /usr/bin
ln -sf /opt/adv-tg/adv-tg %{buildroot}/usr/bin/adv-tg

# Desktop entry integration
install -m 644 %{_sourcedir}/packaging/linux/adv-tg.desktop %{buildroot}/usr/share/applications/adv-tg.desktop

# Scalable vector icon and pixmap fallback
install -m 644 %{_sourcedir}/src/adv_tg/ui/assets/adv-tg.svg %{buildroot}/usr/share/icons/hicolor/scalable/apps/adv-tg.svg
install -m 644 %{_sourcedir}/src/adv_tg/ui/assets/adv-tg.svg %{buildroot}/usr/share/pixmaps/adv-tg.svg

%files
/opt/adv-tg
/usr/bin/adv-tg
/usr/share/applications/adv-tg.desktop
/usr/share/icons/hicolor/scalable/apps/adv-tg.svg
/usr/share/pixmaps/adv-tg.svg

%post
touch --no-create %{_datadir}/icons/hicolor &>/dev/null || :
which update-desktop-database &>/dev/null && update-desktop-database %{_datadir}/applications &>/dev/null || :
which gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache -f -t %{_datadir}/icons/hicolor &>/dev/null || :

%postun
touch --no-create %{_datadir}/icons/hicolor &>/dev/null || :
which update-desktop-database &>/dev/null && update-desktop-database %{_datadir}/applications &>/dev/null || :
which gtk-update-icon-cache &>/dev/null && gtk-update-icon-cache -f -t %{_datadir}/icons/hicolor &>/dev/null || :

%changelog
* Sat Nov 14 2026 Alberto Pico <albertopicolara@gmail.com> - 1.0.1-1
- Release version 1.0.1: GUI contrast improvements in About and Report dialogs,
  compact HUD metric cards, and immediate Clock Drift status synchronization.
* Fri Nov 13 2026 Alberto Pico <albertopicolara@gmail.com> - 1.0.0-1
- Release version 1.0.0: live input channel selection, interference filter, faster
  analysis, update notice; self-contained rpm, deb and AppImage built on EL9.
* Fri Oct 30 2026 Alberto Pico <albertopicolara@gmail.com> - 0.8.0-1
- Release version 0.8.0 (beta): recording analysis (gear train, feature vectors,
  rule-based diagnosis), audio normalization, 44.1 kHz input, no ALSA overflows.
* Wed Oct 28 2026 Alberto Pico <albertopicolara@gmail.com> - 0.7.0-1
- Release version 0.7.0.
* Tue Oct 27 2026 Alberto Pico <albertopicolara@gmail.com> - 0.6.1-1
- Initial RPM release for Advanced Timegrapher standalone PyInstaller package.
