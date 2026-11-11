#
# spec file for package libjxl
#
# Copyright (c) 2026 SUSE LLC and contributors
#
# All modifications and additions to the file contributed by third parties
# remain the property of their copyright owners, unless otherwise agreed
# upon. The license for this file, and modifications and additions to the
# file, is the same license as for the pristine package itself (unless the
# license for the pristine package is not an Open Source License, in which
# case the license is the MIT License). An "Open Source License" is a
# license that conforms to the Open Source Definition (Version 1.9)
# published by the Open Source Initiative.

# Please submit bugfixes or comments via https://bugs.opensuse.org/
#


%define lname   libjxl0_12
%if "" == "gtk"
Name:           libjxl-gtk
%bcond_without gtk
%else
Name:           libjxl
%bcond_with gtk
%endif
Version:        0.12.0
Release:        1.2
Summary:        JPEG XL reference implementation
License:        BSD-3-Clause
URL:            https://jpegxl.info/
#Git-Clone:     https://github.com/libjxl/libjxl
Source:         https://github.com/libjxl/libjxl/archive/refs/tags/v%version.tar.gz
Source1:        baselibs.conf
# `git ls-tree <jxl_tag> third_party/skcms` gives the SKCMS revision needed for a particular libjxl release
%define skcms_commit 96d9171c94b937a1b5f0293de7309ac16311b722
#changes-too-damn-often: https://skia.googlesource.com/skcms/+archive/%%skcms_commit.tar.gz
Source2:        %skcms_commit.tar.gz
Patch1:         notail.patch
BuildRequires:  asciidoc
BuildRequires:  c++_compiler
BuildRequires:  cmake
BuildRequires:  pkg-config
%if %{with gtk}
BuildRequires:  pkgconfig(gdk-pixbuf-2.0) >= 2.36
BuildRequires:  pkgconfig(lcms2) >= 2.12
%endif
BuildRequires:  giflib-devel >= 5.1
BuildRequires:  pkgconfig(OpenEXR)
%ifarch s390x
BuildRequires:  pkgconfig(lcms2) >= 2.12
%endif
BuildRequires:  pkgconfig(libavif)
BuildRequires:  pkgconfig(libbrotlicommon)
BuildRequires:  pkgconfig(libbrotlidec)
BuildRequires:  pkgconfig(libbrotlienc)
BuildRequires:  pkgconfig(libhwy) >= 1.0.7
BuildRequires:  pkgconfig(libjpeg)
BuildRequires:  pkgconfig(libpng)
BuildRequires:  pkgconfig(libwebp)
%if 0%{?sle_version} == 150700
BuildRequires:  gcc10-c++
%endif
%{?suse_build_hwcaps_libs}
%if %{with gtk}
Provides:       bundled(skcms) = 0
%endif

%description
JPEG XL is a raster-graphics file format that supports both lossy and
lossless compression.

This is the reference implementation of JPEG XL, with encoder and decoder.

%package -n %lname
Summary:        Library for encoding and decoding JPEG XL raster graphic images

%description -n %lname
JPEG XL is a raster-graphics file format that supports both lossy and
lossless compression.

%package devel
Summary:        Development for libjxl, an en-/decoder for JPEG XL
Requires:       %lname = %version

%description devel
JPEG XL is a raster-graphics file format that supports both lossy and
lossless compression.

This is the reference implementation of JPEG XL, with encoder and decoder.

%package tools
Summary:        Command-line utilities to convert from/to JPEG XL

%description tools
Command-line utilities to convert from/to JPEG XL.

%package -n gdk-pixbuf-loader-jxl
Summary:        A gdk-pixbuf loader for JPEG-XL using libjxl
Supplements:    (%lname and gdk-pixbuf)
%if %{with gtk}
%gdk_pixbuf_loader_requires
%endif

%description -n gdk-pixbuf-loader-jxl
This package provides a libjxl-based gdk-pixbuf loader for JPEG XL files.

%package -n jxl-thumbnailer
Summary:        Generate thumbnails for JPEG XL files
BuildArch:      noarch

%description -n jxl-thumbnailer
This package provides a thumbnailer to render for JPEG XL file thumbnails,
for example, on file-browsers.

%prep
%setup -n libjxl-%version
# can't use %%autosetup -a2 because archive has no leading path
mkdir -p third_party/skcms/
tar -xf %{SOURCE2} -C third_party/skcms/
%autopatch -p1

%build
%if 0%{?sle_version} == 150700
export CC=gcc-10
export CXX=g++-10
%endif

%cmake -DJPEGXL_FORCE_SYSTEM_HWY=ON -DJPEGXL_FORCE_SYSTEM_BROTLI=ON \
	-DJPEGXL_FORCE_SYSTEM_LCMS2=OFF -DBUILD_TESTING=OFF \
%if %{with gtk}
	-DJPEGXL_ENABLE_PLUGINS=ON -DJPEGXL_ENABLE_SKCMS=ON \
%endif
	-DJPEGXL_ENABLE_SJPEG=OFF -DJPEGXL_ENABLE_DOXYGEN=OFF \
	-DJPEGXL_ENABLE_JPEGLI=ON
%cmake_build

%install
%cmake_install
b="%buildroot"
rm -fv "$b/%_libdir"/*.a
%if %{with gtk}
rm -Rf "$b/%_libdir"/libjxl* "$b/%_bindir" "$b/%_includedir" "$b/%_libdir/pkgconfig" "$b/%_mandir"
%endif

%ldconfig_scriptlets -n %lname

%post -n gdk-pixbuf-loader-jxl
%gdk_pixbuf_loader_post

%postun -n gdk-pixbuf-loader-jxl
%gdk_pixbuf_loader_postun

%if %{without gtk}

%files -n %lname
%license LICENSE
%_libdir/libjxl*.so.*

%files tools
%_bindir/*xl*
%_mandir/man*/*xl*

%files devel
%_includedir/jxl/
%_libdir/libjxl*.so
%_libdir/pkgconfig/*.pc

%else

%files -n gdk-pixbuf-loader-jxl
%_datadir/mime/packages/*
%_libdir/gdk-pixbuf-2.0/*/loaders/libpixbufloader-jxl.so

%files -n jxl-thumbnailer
%dir %_datadir/thumbnailers
%_datadir/thumbnailers/*.thumbnailer

%endif

%changelog
* Thu Jul  2 2026 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.12
  * A new buffering flag is now available in the CLI, alongside
    support for streaming input with buffered output.
  * Introduced the new `jxltran` tool, which supports extracting
    codestreams from `jxlp` boxes and packing raw codestreams into
    `jxlc` boxes.
  * Added the `--reconstruct_jpeg flag` to the `djxl` utility to
    losslessly reconstruct a JPEG and fail if impossible.
  * tools: added support for `ProPhoto`, and `Adobe98`/`AdobeRGB`
    color space names in CLI arguments (cjxl / djxl).
  * decoder API: support for out-of-order `jxlp` boxes (ftyp minor
    version 1).
- jpegli tools were removed by release 0.12
- Delete libjxl-CVE-2025-70103.patch (merged),
  libjxl-CVE-2026-52584.patch (merged),
  system-jpeg.diff (obsolete)
- Add notail.patch
* Thu Jul  2 2026 Jonathan Kang <songchuan.kang@suse.com>
- Add libjxl-CVE-2026-52584.patch: Fix possible overflow in APNG
  decoder (bsc#1271810, CVE-2026-52584).
* Thu May 28 2026 Michael Gorse <mgorse@suse.com>
- Add libjxl-CVE-2025-70103.patch: take EC into accound when
  checking required PNM inmput length (bsc#1266460 CVE-2025-70103).
* Wed Feb 11 2026 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.11.2 (bsc#1258090 bsc#1258091 CVE-2025-12474)
  * Fix tile dimension in low memory rendering pipeline
  * Fix number of channels for gray-to-gray color transform
    [CVE-2026-1837]
  * djxl: reject decoding JXL files if "packed" representation size
    overflows size_t
* Wed Jan 22 2025 Michael Gorse <mgorse@suse.com>
- Remove the gimp plugin. It is for 2.10, and factory now has gimp
  3.0.
* Wed Jan 22 2025 Michael Gorse <mgorse@suse.com>
- Always require lcms2 for the gtk build. It is needed when
  building plugins, and, if gimp is disabled, it needs to be
  specified explicitly.
* Fri Jan 10 2025 Michael Gorse <mgorse@suse.com>
- Disable gimp plugin on SLFO. Gimp is currently unavailable there.
- Re-add lcms2 dependency for s390x. It is used instead of skcms
  on big-endian architectures.
* Tue Nov 26 2024 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.11.1 (boo#1233763 boo#1233785)
  * Huffman lookup table size fix [CVE-2024-11403]
  * Check height limit in modular trees [CVE-2024-11498]
* Fri Sep 13 2024 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.11.0
  * Added Gain Map API: ``JxlGainMapBundle`` struct and API
    functions to read and write gain map
    ``bundlesJxlGainMapWriteBundle`` and ``JxlGainMapReadBundle``
    as well as handling compressed ICC profiles:
    ``JxlICCProfileEncode`` and ``JxlICCProfileDecode``.
  * Decoder API: added ``JXL_DEC_BOX_COMPLETE`` event to signal
    that the output buffer for the current box has received all
    contents.
* Fri Aug 30 2024 Cliff Zhao <qzhao@suse.com>
- Update libjxl.spec:
  Add compiler condition to fix SLE-15-SP7 ppc64le build env.
  (bsc#1229831)
* Sat Jun 29 2024 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.10.3
  * fix bug in huffman-rle-gradient (e1) specialized decode path
* Thu Jun 20 2024 Jan Engelhardt <jengelh@inai.de>
- Enable cjpegli, djpegli tools
- Add system-jpeg.diff
* Fri Mar  8 2024 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.10.2
  * Fix unspecified bugs in (lossless) encoding and streaming mode
* Wed Feb 28 2024 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.10.1
  * reduce allocations and fixing speed regressions since 0.9.0
  * resolve some bug in streaming encoding
* Thu Feb 22 2024 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.10
  * decoder: added ``JxlDecoderGetBoxSizeContents`` for getting the
    size of the content of a box without the headers.
  * encoder: implemented new API functions for streaming encoding.
* Wed Feb  7 2024 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.9.2
  * Fixed some unspecified bugs in the gdk-pixbuf plugin
* Wed Jan  3 2024 Stefan Brüns <stefan.bruens@rwth-aachen.de>
- Remove cjxl/djxl manpages from gtk flavor installation, these
  are part of the tools subpackage from the main flavor.
* Fri Dec 22 2023 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.9
  * Encoder API:
  * Add JxlEncoderSetExtraChannelDistance to adjust the quality
    of extra channels (like alpha) separately.
  * New API functions for streaming encoding.
  * New options for more fine-grained control over metadata
    preservation when using JxlEncoderAddJPEGFrame.
  * New function JxlEncoderSetUpsamplingMode to change the
    upsampling method, e.g. to use nearest-neighbor upsampling
    for pixel art.
  * New function JxlEncoderDistanceFromQuality for convenience to
    calculate a distance given a quality.
  * Decoder API: Implemented JxlDecoderSetOutputColorProfile and
    JxlDecoderSetCms to enable decoding to desired colorspace.
  * Deprecated functions removed from encoder and decoder APIs.
  * Command-line tools:
  * cjxl can now be used to explicitly add/update/strip
    Exif/XMP/JUMBF metadata using the decoder-hints syntax, e.g.
    cjxl input.ppm -x exif=input.exif output.jxl
  * djxl can now be used to extract Exif/XMP/JUMBF metadata.
* Thu Sep 21 2023 Jan Engelhardt <jengelh@inai.de>
- Switch from LCMS to SKCMS: libjxl core can use either, but
  plugins can only use SKCMS. Exercising two CMS simultaneously
  also is silly. Delete 0001-Remove-LCMS-mutex.patch .
* Sat Sep 16 2023 Atri Bhattacharya <badshah400@gmail.com>
- Build plugins:
  * Add _service file to generate skcms tarball (needed to build
    plugins).
  * Add skcms tarball as source and copy extracted dir to
    ./third_party/ so cmake can find it.
  * Split out new packages:
  * gdk-pixbuf-loader-jxl: Pixbuf loader for supported apps.
  * gimp-plugin-jxl: Plugin to allow gimp to work with JPEG XL
    files.
  * jxl-thumbnailer: Thumbnailer and mime files to allow
    generating thumbnails for JPEG XL files.
* Wed Jun 14 2023 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.8.2
  * Fix an integer underflow bug in patch decoding.
* Fri Jun  9 2023 Martin Pluskal <mpluskal@suse.com>
- Build package in correct phase
- Use macros for ldconfig
* Mon Apr 17 2023 Jan Engelhardt <jengelh@inai.de>
- Refresh 0001-Remove-LCMS-mutex.patch
* Fri Feb 10 2023 Dirk Müller <dmueller@suse.com>
- build glibc hwcaps optimized overlay
* Sun Feb  5 2023 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.8.1
  * Allow fast-lossless for 16-bit float input
  * Fix OOB read in exif.h
* Wed Jan 18 2023 Jan Engelhardt <jengelh@inai.de>
- Update to release 0.8
  * API: new function JxlDecoderSetImageBitDepth and
    JxlEncoderSetFrameBitDepth to set the bit depth of buffers.
  * encoder API: add an effort 10 option for lossless
    compression; using this setting requires calling
    JxlEncoderAllowExpertOptions.
- Enable PNG utilities [boo#1205107]
* Mon Dec 19 2022 Antonio Larrosa <alarrosa@suse.com>
- Add patch (applied reversed) to revert the requirement for an
  updated liblcms2 library when we have an older one. This allows
  libjxl to build in SLE15 SP4/SP5 (and Leap 15.4/15.5):
  * 0001-Remove-LCMS-mutex.patch
* Thu Dec 15 2022 Simon Vogl <simon.vogl@gmx.net>
- Added missing baselibs.conf so that 32bit library packages
  become available
* Thu Sep 22 2022 Enrico Belleri <idesmi@protonmail.com>
- Update to 0.7.0:
  * new functions for retrieving associated alpha channel with
    premultiplied or unpremultiplied colors, for blending
    information for extra channels in the non-coalesced case, for
    getting the intended downsampling ratio of progressive steps,
    for disabling rendering of spot colors.
  * Added ability to add metadata boxes, to set several encoder
    options, to check required codestream compatibility level,
    for force-emitting the box-based container format, to store
    JPEG metadata for lossless reconstruction, to encode
    arbitrary extra channels.
* Sat Jul 23 2022 Jan Engelhardt <jengelh@inai.de>
- Initial package (version 0.7~779.f5d6e29) build.opensuse.org
