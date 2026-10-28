from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from pathlib import Path
import struct
import shutil
import subprocess
import re

from PIL import Image, ImageOps

from .tools import run_tool, app_directory


@dataclass(frozen=True)
class BmpSpec:
    width: int
    height: int
    bits_per_pixel: int
    compression: int
    dib_size: int
    pixel_offset: int
    file_size_header: int
    actual_size: int
    image_size_header: int
    x_pixels_per_meter: int
    y_pixels_per_meter: int
    colors_used: int
    colors_important: int
    top_down: bool
    row_stride: int
    palette_entries: int

    def to_dict(self) -> dict:
        return asdict(self)


SUPPORTED_CHANGELOGO_MARKERS = (
    "Change Logo Aptio V",
    "5.02.0022",
    "ROM image does not contain",
    "Usage for Multi Logo support:",
    "Image does not contain multiple splash logos",
)


def sha256_bytes(data: bytes) -> str:
    return sha256(data).hexdigest()


def sha256_path(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def inspect_bmp(path: Path) -> BmpSpec:
    data = path.read_bytes()
    if len(data) < 54 or data[:2] != b"BM":
        raise ValueError(f"{path.name} is not a Windows BMP file")
    file_size_header = struct.unpack_from("<I", data, 2)[0]
    pixel_offset = struct.unpack_from("<I", data, 10)[0]
    dib_size = struct.unpack_from("<I", data, 14)[0]
    if dib_size < 40 or len(data) < 14 + dib_size:
        raise ValueError(f"{path.name} uses an unsupported BMP DIB header (size {dib_size})")
    width, signed_height = struct.unpack_from("<ii", data, 18)
    planes, bpp = struct.unpack_from("<HH", data, 26)
    compression = struct.unpack_from("<I", data, 30)[0]
    image_size_header = struct.unpack_from("<I", data, 34)[0]
    xppm, yppm = struct.unpack_from("<ii", data, 38)
    colors_used = struct.unpack_from("<I", data, 46)[0]
    colors_important = struct.unpack_from("<I", data, 50)[0]
    if width <= 0 or signed_height == 0 or planes != 1:
        raise ValueError(f"{path.name} has an invalid BMP geometry/header")
    if bpp not in (1, 4, 8, 16, 24, 32):
        raise ValueError(f"{path.name} has unsupported BMP bit depth {bpp}")
    height = abs(signed_height)
    row_stride = ((width * bpp + 31) // 32) * 4
    if pixel_offset < 14 + dib_size or pixel_offset > len(data):
        raise ValueError(f"{path.name} has an invalid BMP pixel offset")
    palette_bytes = max(0, pixel_offset - 14 - dib_size)
    palette_entries = palette_bytes // 4 if bpp <= 8 else 0
    minimum_pixel_bytes = row_stride * height
    if pixel_offset + minimum_pixel_bytes > len(data):
        raise ValueError(f"{path.name} has truncated BMP pixel data")
    return BmpSpec(
        width=width,
        height=height,
        bits_per_pixel=bpp,
        compression=compression,
        dib_size=dib_size,
        pixel_offset=pixel_offset,
        file_size_header=file_size_header,
        actual_size=len(data),
        image_size_header=image_size_header,
        x_pixels_per_meter=xppm,
        y_pixels_per_meter=yppm,
        colors_used=colors_used,
        colors_important=colors_important,
        top_down=signed_height < 0,
        row_stride=row_stride,
        palette_entries=palette_entries,
    )


def _image_rgb_sha256(path: Path) -> str:
    with Image.open(path) as image:
        rgb = image.convert("RGB")
        return sha256(rgb.tobytes()).hexdigest()


def validate_changelogo_exe(path: Path) -> dict:
    if not path or not path.is_file():
        raise FileNotFoundError("ChangeLogo.exe not found")
    data = path.read_bytes()
    missing = [
        marker for marker in SUPPORTED_CHANGELOGO_MARKERS
        if marker.encode("ascii") not in data and marker.encode("utf-16le") not in data
    ]
    if missing:
        raise ValueError(f"ChangeLogo.exe does not match the expected AMI Aptio V utility; missing markers: {missing}")
    return {
        "path": str(path),
        "size": len(data),
        "sha256": sha256_bytes(data),
        "expected_version": "5.02.0022",
    }


def _run_changelogo(changelogo: Path, args: list[str], cwd: Path, timeout: int = 120) -> dict:
    command = [str(changelogo), *args]
    result = run_tool(command, cwd=cwd, timeout=timeout)
    diagnostic = (
        f"Command: {subprocess.list2cmdline(command)}\n"
        f"Working directory: {cwd}\n"
        f"Exit code: {result.returncode}\n\n"
        f"Output:\n{result.stdout}"
    )
    if result.returncode != 0:
        raise RuntimeError(f"ChangeLogo failed with exit code {result.returncode}; see session log for complete ChangeLogo output")
    return {"stdout": result.stdout, "diagnostic": diagnostic, "returncode": result.returncode}


def extract_splash(rom: Path, output_bmp: Path, changelogo: Path, cwd: Path) -> dict:
    output_bmp.unlink(missing_ok=True)
    result = _run_changelogo(changelogo, ["/i", str(rom), "/e", str(output_bmp), "/f"], cwd)
    if not output_bmp.is_file():
        raise RuntimeError("ChangeLogo reported success but did not create the extracted splash image")
    spec = inspect_bmp(output_bmp)
    return {
        "path": str(output_bmp),
        "spec": spec.to_dict(),
        "sha256": sha256_path(output_bmp),
        "rgb_sha256": _image_rgb_sha256(output_bmp),
        "tool": result,
    }


def _same_compatibility_spec(a: BmpSpec, b: BmpSpec) -> bool:
    fields = (
        "width", "height", "bits_per_pixel", "compression", "dib_size",
        "pixel_offset", "file_size_header", "image_size_header",
        "x_pixels_per_meter", "y_pixels_per_meter",
        "colors_used", "colors_important", "top_down", "row_stride",
        "palette_entries", "actual_size",
    )
    return all(getattr(a, field) == getattr(b, field) for field in fields)


def _supported_4bit_replacement(spec: BmpSpec) -> bool:
    """Accept a complete canonical 4-bit BMP without shrinking it to the old logo.

    800x600 is the bundled master canvas. ChangeLogo must still confirm that the
    encoded logo fits the ROM, and final extraction must match its full pixels.
    These are file/build checks, not proof of a particular firmware's rendering.
    """
    return (
        spec.bits_per_pixel == 4 and spec.compression == 0 and not spec.top_down
        and spec.dib_size == 40 and spec.pixel_offset == 118
        and spec.palette_entries == 16 and spec.colors_used in (0, 16)
        and 0 <= spec.colors_important <= 16
        and 0 < spec.width <= 800 and 0 < spec.height <= 600
        and spec.image_size_header in (0, spec.row_stride * spec.height)
        and spec.file_size_header == spec.actual_size
        and spec.actual_size == spec.pixel_offset + spec.row_stride * spec.height
    )


def select_splash_source(source_image: Path | None, original_bmp: Path) -> Path:
    """Choose SenorSpace from the extracted bitmap, or preserve an explicit custom choice."""
    if source_image is not None:
        if not source_image.is_file():
            raise FileNotFoundError(f"Selected custom splash BMP is missing: {source_image}")
        return source_image.resolve()

    original = inspect_bmp(original_bmp)
    if original.compression != 0 or original.top_down or original.bits_per_pixel not in (4, 8):
        raise ValueError("Automatic SenorSpace selection requires a bottom-up 4-bit or 8-bit BI_RGB original")
    name = "SenorSpace-XD3-4bit.bmp" if original.bits_per_pixel == 4 else "SenorSpace.bmp"
    roots = (app_directory() / "assets", Path(__file__).resolve().parent.parent / "assets")
    for root in dict.fromkeys(roots):
        candidate = root / name
        if not candidate.is_file():
            continue
        candidate_spec = inspect_bmp(candidate)
        if original.bits_per_pixel == 4:
            if not _supported_4bit_replacement(candidate_spec):
                continue
        elif candidate_spec.bits_per_pixel != 8 or candidate_spec.compression != 0 or candidate_spec.top_down:
            continue
        return candidate.resolve()
    raise ValueError(
        f"No compatible bundled SenorSpace bitmap for the extracted "
        f"{original.width}x{original.height} {original.bits_per_pixel}-bit splash; "
        f"required asset: {name}"
    )


def prepare_splash_replacement(source_image: Path | None, original_bmp: Path, work_root: Path, slot: str) -> tuple[Path, dict]:
    selected = select_splash_source(source_image, original_bmp)
    normalized = _replacement_work_path(work_root, slot, selected)
    info = normalize_replacement_bmp(selected, original_bmp, normalized)
    info["selection_mode"] = "automatic SenorSpace" if source_image is None else "custom image"
    info["selected_source"] = str(selected)
    return normalized, info


def _write_indexed_bmp_like_original(image: Image.Image, original_path: Path, output_bmp: Path) -> None:
    original_data = original_path.read_bytes()
    original = inspect_bmp(original_path)
    if original.bits_per_pixel != 8 or original.compression != 0 or original.top_down:
        raise ValueError("Only bottom-up 8-bit BI_RGB originals are supported for exact-format normalization")
    if original.dib_size != 40:
        raise ValueError(f"Existing splash uses DIB header size {original.dib_size}; exact-format normalizer currently supports BITMAPINFOHEADER (40) only")

    implied_colors = original.colors_used if original.colors_used else 256
    if original.palette_entries < implied_colors:
        raise ValueError(
            f"Existing splash palette area contains only {original.palette_entries} entries but header implies {implied_colors}"
        )
    palette_count = max(2, min(256, implied_colors))

    fitted = ImageOps.fit(
        image.convert("RGB"),
        (original.width, original.height),
        method=Image.Resampling.LANCZOS,
        centering=(0.5, 0.5),
    )
    indexed = fitted.quantize(
        colors=palette_count,
        method=Image.Quantize.MEDIANCUT,
        dither=Image.Dither.FLOYDSTEINBERG,
    )

    palette = indexed.getpalette() or []
    palette_bytes = bytearray()
    for index in range(original.palette_entries):
        base = index * 3
        if index < palette_count and base + 2 < len(palette):
            r, g, b = palette[base:base + 3]
        else:
            r = g = b = 0
        palette_bytes += bytes((b, g, r, 0))

    rows = bytearray()
    raw_indices = indexed.tobytes()
    width = original.width
    pad_len = original.row_stride - width
    for y in range(original.height - 1, -1, -1):
        start = y * width
        rows += raw_indices[start:start + width]
        if pad_len:
            rows += b"\x00" * pad_len

    # Reuse the original DIB header wholesale so all less-obvious compatibility
    # fields remain identical. Dimensions and format are unchanged by design.
    dib = bytearray(original_data[14:14 + original.dib_size])
    # Keep the original biSizeImage field exactly as encoded. Some AMI BMPs
    # contain a slightly non-canonical value even though the pixel array itself
    # is valid; compatibility is safer when that field is preserved verbatim.
    # Preserve original colors-used / colors-important values exactly.
    struct.pack_into("<I", dib, 32, original.colors_used)
    struct.pack_into("<I", dib, 36, original.colors_important)

    palette_start = 14 + original.dib_size
    gap_len = original.pixel_offset - palette_start - len(palette_bytes)
    if gap_len < 0:
        raise ValueError("Normalized palette does not fit in the original BMP header/palette area")
    pre_pixels = bytes(palette_bytes) + b"\x00" * gap_len

    minimum_size = original.pixel_offset + len(rows)
    target_size = max(minimum_size, original.actual_size)
    tail = b"\x00" * (target_size - minimum_size)
    file_header = bytearray(original_data[:14])
    file_header[:2] = b"BM"
    struct.pack_into("<I", file_header, 2, target_size)
    struct.pack_into("<I", file_header, 10, original.pixel_offset)

    output_bmp.write_bytes(bytes(file_header) + bytes(dib) + pre_pixels + bytes(rows) + tail)




def _replacement_work_path(work_root: Path, slot: str, source_image: Path) -> Path:
    """Return a temp BMP path whose *basename* matches the user-selected BMP.

    AMI ChangeLogo stores the replacement file name in the logo FFS UI section.
    The manual ChangeLogo GUI therefore produces a different firmware object if
    the toolkit invents a temporary basename such as
    ``normalized-default-splash.bmp``.  Keep per-slot subdirectories for
    isolation, but preserve the exact source basename passed to ChangeLogo.
    """
    name = source_image.name
    if not name or Path(name).suffix.lower() != ".bmp":
        raise ValueError("Splash replacement source must have a .bmp filename")
    safe_slot = re.sub(r"[^A-Za-z0-9_.-]+", "-", slot).strip("-.") or "splash"
    return work_root / safe_slot / name

def normalize_replacement_bmp(source: Path, original_splash: Path, output_bmp: Path) -> dict:
    original = inspect_bmp(original_splash)
    supplied = inspect_bmp(source)

    # Keep valid 4-bit replacements at their supplied dimensions; fitting a tall
    # character into the old panoramic logo would discard most of its detail.
    if original.bits_per_pixel not in (4, 8) or original.compression != 0:
        raise ValueError(
            "Existing splash is not a supported 4-bit or 8-bit uncompressed BMP; automatic logo normalization refuses to force a type mismatch"
        )
    if original.top_down:
        raise ValueError("Top-down BMP splash layout is not supported safely")

    output_bmp.parent.mkdir(parents=True, exist_ok=True)
    exact = _same_compatibility_spec(supplied, original)
    if exact:
        shutil.copy2(source, output_bmp)
        operation = "copied unchanged; supplied BMP already matches the extracted splash compatibility format"
    elif original.bits_per_pixel == 4:
        if not _supported_4bit_replacement(supplied):
            raise ValueError(
                "4-bit replacement must be a complete bottom-up uncompressed 4-bit BMP "
                "with a 40-byte DIB, 16-entry palette, correct size fields and dimensions up to 800x600"
            )
        shutil.copy2(source, output_bmp)
        operation = "copied unchanged; preserved supplied 4-bit artwork dimensions; ChangeLogo fit and final pixel verification required"
    else:
        with Image.open(source) as image:
            _write_indexed_bmp_like_original(image, original_splash, output_bmp)
        operation = "resized/cropped and re-encoded to the extracted splash's exact BMP compatibility format"

    normalized = inspect_bmp(output_bmp)
    expected = supplied if original.bits_per_pixel == 4 else original
    if not _same_compatibility_spec(normalized, expected):
        raise RuntimeError(
            "Prepared splash does not match its expected BMP specification: "
            f"expected={expected.to_dict()} prepared={normalized.to_dict()}"
        )
    if original.bits_per_pixel == 4:
        with Image.open(output_bmp) as image:
            image.load()
            if len(image.convert("RGB").getcolors(maxcolors=16) or []) == 0:
                raise ValueError("4-bit replacement does not decode to at most 16 colours")

    return {
        "operation": operation,
        "original_spec": original.to_dict(),
        "supplied_spec": supplied.to_dict(),
        "normalized_spec": normalized.to_dict(),
        "source_sha256": sha256_path(source),
        "normalized_sha256": sha256_path(output_bmp),
        "normalized_rgb_sha256": _image_rgb_sha256(output_bmp),
        "normalized_path": str(output_bmp),
        "replacement_basename": output_bmp.name,
    }



_MULTI_NONE_MARKERS = (
    "does not contain multiple splash logos",
    "does not contain any multiple splash logo",
    "does not contain any common multiple splash logo",
    "does not contain any multiple splash logo for platform",
)


def parse_multiple_logo_list(stdout: str) -> list[dict]:
    """Parse ChangeLogo 5.02.0022 `/l /m` output.

    ChangeLogo documents multi-logo numbering as 1..N and prints a table headed
    `No | FileName | LogoType`.  We deliberately require explicit table rows or
    an explicit "no multiple splash logos" diagnostic; unknown output is not
    silently treated as an empty list.
    """
    lower = stdout.lower()
    if any(marker in lower for marker in _MULTI_NONE_MARKERS):
        return []

    rows: list[dict] = []
    seen: set[int] = set()
    for raw in stdout.splitlines():
        line = raw.strip()
        if not line or "|" not in line:
            continue
        fields = [field.strip() for field in line.strip("|").split("|")]
        if not fields:
            continue
        if not re.fullmatch(r"\d+", fields[0]):
            continue
        number = int(fields[0], 10)
        if number <= 0:
            raise RuntimeError(f"ChangeLogo returned invalid multiple-logo number {number}")
        if number in seen:
            raise RuntimeError(f"ChangeLogo returned duplicate multiple-logo number {number}")
        seen.add(number)
        rows.append({
            "number": number,
            "filename": fields[1] if len(fields) > 1 else "",
            "logo_type": fields[2] if len(fields) > 2 else "",
            "raw_line": raw,
        })

    rows.sort(key=lambda item: item["number"])
    if rows:
        expected = list(range(1, rows[-1]["number"] + 1))
        actual = [item["number"] for item in rows]
        if actual != expected:
            raise RuntimeError(
                "ChangeLogo multiple-logo numbering is not the documented contiguous 1..N sequence: "
                f"{actual}"
            )
        return rows

    # A successful `/l /m` that contains the documented table heading but no
    # rows is acceptable as an empty table.  Anything else is ambiguous and is
    # rejected so that the toolkit cannot accidentally leave hidden splash
    # entries untouched.
    if "filename" in lower and "logotype" in lower and "no" in lower:
        return []
    raise RuntimeError(
        "Could not parse ChangeLogo multiple-splash inventory. The toolkit will not assume that no additional logos exist.\n"
        + stdout
    )


def list_multiple_splash_logos(rom: Path, changelogo: Path, cwd: Path) -> dict:
    # Some ChangeLogo builds return a non-zero process code when the firmware
    # simply has no numbered multi-splash table.  For inventory this is not an
    # operational failure if (and only if) the utility explicitly says that no
    # multiple splash logos exist.
    command = [str(changelogo), "/i", str(rom), "/l", "/m"]
    result = run_tool(command, cwd=cwd, timeout=120)
    diagnostic = (
        f"Command: {subprocess.list2cmdline(command)}\n"
        f"Working directory: {cwd}\n"
        f"Exit code: {result.returncode}\n\n"
        f"Output:\n{result.stdout}"
    )
    try:
        logos = parse_multiple_logo_list(result.stdout)
    except Exception:
        if result.returncode != 0:
            raise RuntimeError(
                f"ChangeLogo multi-splash list failed with exit code {result.returncode}; see session log for complete ChangeLogo output"
            )
        raise
    if result.returncode != 0 and logos:
        raise RuntimeError(
            f"ChangeLogo returned a numbered multi-splash table but exited with code {result.returncode}; see session log for complete ChangeLogo output"
        )
    tool = {"stdout": result.stdout, "diagnostic": diagnostic, "returncode": result.returncode}
    return {
        "count": len(logos),
        "logos": logos,
        "tool": tool,
    }


def extract_multiple_splash(
    rom: Path,
    output_bmp: Path,
    number: int,
    changelogo: Path,
    cwd: Path,
) -> dict:
    if number <= 0:
        raise ValueError("multiple splash logo number must be >= 1")
    output_bmp.unlink(missing_ok=True)
    result = _run_changelogo(
        changelogo,
        ["/i", str(rom), "/e", str(output_bmp), "/m", str(number), "/f"],
        cwd,
    )
    if not output_bmp.is_file():
        raise RuntimeError(
            f"ChangeLogo reported success but did not extract multiple splash logo #{number}"
        )
    try:
        spec = inspect_bmp(output_bmp)
    except Exception as exc:
        raise ValueError(
            f"Multiple splash logo #{number} is not a supported BMP. "
            "The toolkit refuses to force the supplied BMP into a different logo format. "
            f"{type(exc).__name__}: {exc}"
        ) from exc
    return {
        "number": number,
        "path": str(output_bmp),
        "spec": spec.to_dict(),
        "sha256": sha256_path(output_bmp),
        "rgb_sha256": _image_rgb_sha256(output_bmp),
        "tool": result,
    }


def _validate_rom_output(before: Path, after: Path, context: str, *, allow_identical: bool = False) -> None:
    if not after.is_file():
        raise RuntimeError(f"ChangeLogo reported success but did not produce a ROM while {context}")
    if after.stat().st_size != before.stat().st_size:
        raise RuntimeError(f"ChangeLogo output ROM size differs from input ROM while {context}")
    if not allow_identical and sha256_path(after) == sha256_path(before):
        raise RuntimeError(f"ChangeLogo reported success but the ROM is byte-identical while {context}")


def _verify_bmp_entry(extracted_bmp: Path, normalized_bmp: Path, label: str) -> dict:
    normalized_spec = inspect_bmp(normalized_bmp)
    verified_spec = inspect_bmp(extracted_bmp)
    if not _same_compatibility_spec(verified_spec, normalized_spec):
        raise RuntimeError(
            f"{label}: extracted final splash compatibility specification does not match normalized replacement"
        )
    expected_rgb = _image_rgb_sha256(normalized_bmp)
    actual_rgb = _image_rgb_sha256(extracted_bmp)
    if actual_rgb != expected_rgb:
        raise RuntimeError(f"{label}: extracted final splash pixels do not match the replacement image")
    return {
        "spec": verified_spec.to_dict(),
        "sha256": sha256_path(extracted_bmp),
        "rgb_sha256": actual_rgb,
    }


def replace_splash(rom: Path, output_rom: Path, source_image: Path | None, changelogo: Path, temp_dir: Path) -> dict:
    """Replace the ordinary splash and every ChangeLogo multi-splash entry.

    ChangeLogo 5.02.0022 has two different CLI paths for splash graphics:
    the normal/default splash (`/r`) and the numbered multi-logo table
    (`/l /m`, `/r ... /m N`).  The toolkit inventories the numbered table
    before changing anything, normalizes the supplied BMP independently to
    every original entry, replaces all entries, and re-extracts every entry
    from the final ROM for verification.

    UX v1 also preserves the user-selected BMP basename for every ChangeLogo
    replacement. ChangeLogo embeds that basename in the logo FFS UI section.
    Earlier toolkit versions passed an invented temporary filename such as
    `normalized-default-splash.bmp`, making their logo FFS differ from the
    proven manual ChangeLogo-GUI output even when the BMP bytes were identical.
    """
    temp_dir.mkdir(parents=True, exist_ok=True)
    tool_info = validate_changelogo_exe(changelogo)

    # Inventory everything before the first modification. This prevents a
    # replacement of a shared logo from changing the evidence used to determine
    # what other entries existed in the input ROM.
    original_default_bmp = temp_dir / "original-default-splash.bmp"
    original_default = extract_splash(rom, original_default_bmp, changelogo, temp_dir)
    normalized_default_bmp, default_normalization = prepare_splash_replacement(
        source_image, original_default_bmp, temp_dir, "default"
    )

    multi_inventory = list_multiple_splash_logos(rom, changelogo, temp_dir)
    multi_plans: list[dict] = []
    for item in multi_inventory["logos"]:
        number = item["number"]
        original_bmp = temp_dir / f"original-multi-{number}.bmp"
        original = extract_multiple_splash(rom, original_bmp, number, changelogo, temp_dir)
        normalized_bmp, normalization = prepare_splash_replacement(source_image, original_bmp, temp_dir, f"multi-{number}")
        multi_plans.append({
            "number": number,
            "inventory": item,
            "original": original,
            "normalization": normalization,
            "normalized_path": normalized_bmp,
        })

    diagnostics: list[str] = [
        f"--- Default splash source selection ---\nmode={default_normalization['selection_mode']}; "
        f"source={default_normalization['selected_source']}; original={default_normalization['original_spec']}",
        "--- ChangeLogo extract original default ---\n" + original_default["tool"]["diagnostic"],
        "--- ChangeLogo list multiple splash logos ---\n" + multi_inventory["tool"]["diagnostic"],
    ]
    for plan in multi_plans:
        diagnostics.append(
            f"--- Multiple splash #{plan['number']} source selection ---\n"
            f"mode={plan['normalization']['selection_mode']}; source={plan['normalization']['selected_source']}\n"
        )
        diagnostics.append(
            f"--- ChangeLogo extract original multiple #{plan['number']} ---\n"
            + plan["original"]["tool"]["diagnostic"]
        )

    # Default/single splash: retain the fit-test safety check supported by the
    # utility. If it already contains the desired pixels/spec, no write is made.
    current_rom = rom
    default_preexisting = (
        _same_compatibility_spec(inspect_bmp(original_default_bmp), inspect_bmp(normalized_default_bmp))
        and original_default["rgb_sha256"] == default_normalization["normalized_rgb_sha256"]
    )
    default_replacement = None
    default_fit = None
    if not default_preexisting:
        default_fit = _run_changelogo(
            changelogo, ["/i", str(current_rom), "/t", str(normalized_default_bmp)], temp_dir
        )
        diagnostics.append("--- ChangeLogo test-fit default ---\n" + default_fit["diagnostic"])
        fit_text = default_fit["stdout"].lower()
        if "will fit" not in fit_text and "fit into" not in fit_text:
            raise RuntimeError(
                "ChangeLogo test-fit command did not positively confirm that the normalized default splash fits"
            )
        stage = temp_dir / "stage-default-splash.rom"
        stage.unlink(missing_ok=True)
        default_replacement = _run_changelogo(
            changelogo,
            ["/i", str(current_rom), "/o", str(stage), "/r", str(normalized_default_bmp)],
            temp_dir,
        )
        diagnostics.append("--- ChangeLogo replace default ---\n" + default_replacement["diagnostic"])
        _validate_rom_output(current_rom, stage, "replacing the default splash")
        current_rom = stage

    # The numbered table must still contain the same entry numbers after the
    # default replacement.  A changed inventory is treated as unsafe rather
    # than guessing at new indices.
    after_default_inventory = list_multiple_splash_logos(current_rom, changelogo, temp_dir)
    diagnostics.append(
        "--- ChangeLogo re-list multiple splash logos after default replacement ---\n"
        + after_default_inventory["tool"]["diagnostic"]
    )
    before_numbers = [item["number"] for item in multi_inventory["logos"]]
    after_numbers = [item["number"] for item in after_default_inventory["logos"]]
    if before_numbers != after_numbers:
        raise RuntimeError(
            "ChangeLogo multiple-splash numbering changed after default replacement; "
            f"before={before_numbers}, after={after_numbers}. Refusing to guess."
        )

    multi_results: list[dict] = []
    for plan in multi_plans:
        number = plan["number"]
        normalized_bmp = Path(plan["normalized_path"])

        # A default/shared replacement can legitimately update a numbered entry
        # as a side effect. Detect that and avoid an unnecessary second rewrite.
        current_extract = temp_dir / f"current-multi-{number}.bmp"
        current_info = extract_multiple_splash(
            current_rom, current_extract, number, changelogo, temp_dir
        )
        diagnostics.append(
            f"--- ChangeLogo inspect current multiple #{number} ---\n"
            + current_info["tool"]["diagnostic"]
        )
        already_current = (
            _same_compatibility_spec(inspect_bmp(current_extract), inspect_bmp(normalized_bmp))
            and current_info["rgb_sha256"] == _image_rgb_sha256(normalized_bmp)
        )

        replace_result = None
        if not already_current:
            stage = temp_dir / f"stage-multi-{number}.rom"
            stage.unlink(missing_ok=True)
            replace_result = _run_changelogo(
                changelogo,
                [
                    "/i", str(current_rom), "/o", str(stage), "/r", str(normalized_bmp),
                    "/m", str(number),
                ],
                temp_dir,
            )
            diagnostics.append(
                f"--- ChangeLogo replace multiple #{number} ---\n" + replace_result["diagnostic"]
            )
            _validate_rom_output(current_rom, stage, f"replacing multiple splash logo #{number}")
            current_rom = stage

        verify_bmp = temp_dir / f"verify-multi-{number}.bmp"
        verified_extract = extract_multiple_splash(
            current_rom, verify_bmp, number, changelogo, temp_dir
        )
        diagnostics.append(
            f"--- ChangeLogo verify multiple #{number} ---\n"
            + verified_extract["tool"]["diagnostic"]
        )
        verification = _verify_bmp_entry(
            verify_bmp, normalized_bmp, f"multiple splash logo #{number}"
        )
        multi_results.append({
            "number": number,
            "inventory": plan["inventory"],
            "original": plan["original"],
            "normalization": plan["normalization"],
            "already_current_after_default": already_current,
            "replacement": replace_result,
            "verified": verified_extract,
            "verification": verification,
        })

    # Final inventory must still be exactly the same numbered set, then verify
    # the ordinary/default splash one more time in case a numbered logo was
    # shared with it and ChangeLogo updated both together.
    final_inventory = list_multiple_splash_logos(current_rom, changelogo, temp_dir)
    diagnostics.append(
        "--- ChangeLogo final multiple-splash inventory ---\n"
        + final_inventory["tool"]["diagnostic"]
    )
    final_numbers = [item["number"] for item in final_inventory["logos"]]
    if final_numbers != before_numbers:
        raise RuntimeError(
            "Final ChangeLogo multiple-splash inventory differs from input; "
            f"before={before_numbers}, final={final_numbers}"
        )

    verify_default_bmp = temp_dir / "verified-default-splash.bmp"
    verified_default = extract_splash(current_rom, verify_default_bmp, changelogo, temp_dir)
    diagnostics.append(
        "--- ChangeLogo final verify default ---\n" + verified_default["tool"]["diagnostic"]
    )
    default_verification = _verify_bmp_entry(
        verify_default_bmp, normalized_default_bmp, "default splash logo"
    )

    # Re-verify every numbered entry against the final ROM, not merely the
    # intermediate ROM immediately after its own replacement.
    final_multi_verified: list[dict] = []
    for plan in multi_plans:
        number = plan["number"]
        verify_bmp = temp_dir / f"final-verify-multi-{number}.bmp"
        verified = extract_multiple_splash(
            current_rom, verify_bmp, number, changelogo, temp_dir
        )
        diagnostics.append(
            f"--- ChangeLogo final verify multiple #{number} ---\n"
            + verified["tool"]["diagnostic"]
        )
        verification = _verify_bmp_entry(
            verify_bmp,
            Path(plan["normalized_path"]),
            f"final multiple splash logo #{number}",
        )
        final_multi_verified.append({
            "number": number,
            "verified": verified,
            "verification": verification,
        })

    output_rom.unlink(missing_ok=True)
    shutil.copy2(current_rom, output_rom)
    # All final entries have been re-extracted and checked above. An unchanged
    # ROM is valid only when every requested image was already present and no
    # replacement command was needed. A real replacement must still change it.
    already_present = default_replacement is None and all(
        item['replacement'] is None for item in multi_results
    )
    _validate_rom_output(rom, output_rom, "publishing the complete splash replacement",
                         allow_identical=already_present)

    return {
        "tool": tool_info,
        "already_present": already_present,
        # Backward-compatible keys for callers/report readers that expect the
        # v1.8/v1.9 single-splash result structure.
        "original": original_default,
        "normalization": default_normalization,
        "fit": default_fit,
        "replacement": default_replacement,
        "verified": verified_default,
        # UX v1 complete inventory/result data.
        "default": {
            "original": original_default,
            "normalization": default_normalization,
            "preexisting": default_preexisting,
            "fit": default_fit,
            "replacement": default_replacement,
            "verified": verified_default,
            "verification": default_verification,
        },
        "multiple_inventory": multi_inventory,
        "multiple_logo_count": len(multi_results),
        "multiple_logos": multi_results,
        "final_multiple_inventory": final_inventory,
        "final_multiple_verified": final_multi_verified,
        "output_sha256": sha256_path(output_rom),
        "diagnostic": "\n\n".join(diagnostics),
    }
