"""Разбор .osu.

Источник: osu.Game/Beatmaps/Formats/LegacyBeatmapDecoder.cs,
          osu.Game/Rulesets/Objects/Legacy/ConvertHitObjectParser.cs
Пин: ppy/osu @ 52461f1b (Version 20260706)

Разбирается только то, что нужно для расчёта: General, Difficulty, TimingPoints,
HitObjects и брейки из Events. Остальное в Events, Colours, сэмплы и сториборд
игнорируются.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

from ..f32 import Vec2, f32
from .objects import (
    BEZIER,
    CATMULL,
    LINEAR,
    PERFECT_CURVE,
    HitCircle,
    HitObject,
    PathControlPoint,
    PathType,
    Slider,
    Spinner,
    SplineType,
)

__all__ = ["Beatmap", "BreakPeriod", "TimingPoint", "DifficultyPoint", "decode_beatmap", "BeatmapParseError"]

#: LegacyBeatmapEncoder.FIRST_LAZER_VERSION. До неё координаты усекаются до int,
#: а вырожденные дуги схлопываются в прямую — ради совместимости со stable.
FIRST_LAZER_VERSION = 128

#: Precision.FLOAT_EPSILON из osu-framework. Порог коллинеарности дуги.
FLOAT_EPSILON = f32(1e-3)

#: Parsing.MAX_PARSE_VALUE — int.MaxValue.
MAX_PARSE_VALUE: Final[float] = 2147483647.0

#: Parsing.MAX_COORDINATE_VALUE. Предел для координат и длины слайдера.
MAX_COORDINATE_VALUE: Final[float] = 131072.0

#: ConvertHitObjectParser: слайдер с таким числом повторов отвергается целиком.
MAX_REPEAT_COUNT: Final[int] = 9000

#: ConvertHitObjectParser: спиннер всегда ставится в центр поля,
#: `new Vector2(512, 384) / 2`. Координаты из файла игнорируются, и у части
#: старых карт они действительно другие.
SPINNER_POSITION_X: Final[float] = 256.0
SPINNER_POSITION_Y: Final[float] = 192.0

#: Сдвига времён (EARLY_VERSION_TIMING_OFFSET, 24 мс для карт ниже v5) здесь нет:
#: расчёт сложности идёт через LegacyDifficultyCalculatorBeatmapDecoder
#: с ApplyOffsets = false, и osu-tools регистрирует именно его.

_SECTION_RE = re.compile(r"^\[(?P<name>[^\]]+)\]$")
_VERSION_RE = re.compile(r"^osu file format v(?P<version>\d+)")


class BeatmapParseError(ValueError):
    pass


@dataclass
class TimingPoint:
    """Неунаследованная точка: задаёт длительность доли."""

    time: float
    beat_length: float

    def __post_init__(self) -> None:
        # TimingControlPoint.BeatLengthBindable зажимает значение при записи.
        # Помимо кривых карт это спасает от деления на ноль в скорости слайдера.
        self.beat_length = min(max(self.beat_length, 6.0), 60000.0)


@dataclass
class DifficultyPoint:
    """Унаследованная точка: множитель скорости слайдера.

    generate_ticks выключается тайминг-поинтом с beatLength = NaN. Такой слайдер
    получает TickDistance = +Infinity и остаётся без тиков — см. карту nan-slider.
    """

    time: float
    slider_velocity: float
    generate_ticks: bool = True

    def __post_init__(self) -> None:
        # DifficultyControlPoint.SliderVelocityBindable зажимает значение при записи.
        self.slider_velocity = min(max(self.slider_velocity, 0.1), 10.0)


@dataclass(frozen=True)
class BreakPeriod:
    """Перерыв из секции Events. Нужен только легаси-скорингу: он вычитается
    из длительности карты при расчёте множителя ScoreV1."""

    start_time: float
    end_time: float


@dataclass
class Beatmap:
    format_version: int = 14
    mode: int = 0
    stack_leniency: float = 0.7

    drain_rate: float = 5.0
    circle_size: float = 5.0
    overall_difficulty: float = 5.0
    approach_rate: float = 5.0
    slider_multiplier: float = 1.4
    slider_tick_rate: float = 1.0

    breaks: list[BreakPeriod] = field(default_factory=list)
    timing_points: list[TimingPoint] = field(default_factory=list)
    difficulty_points: list[DifficultyPoint] = field(default_factory=list)
    hit_objects: list[HitObject] = field(default_factory=list)

    #: Расчёт сложности меняет карту на месте, и повторный прогон дал бы неверный
    #: результат: настройки сложности получили бы поправки модов дважды, а под HR
    #: карта отразилась бы обратно. Флаг превращает это в явную ошибку.
    processed: bool = False

    #: Сколько строк файла разобрать не удалось. osu! их пропускает и считает
    #: карту дальше (см. _decode), поэтому счётчик — единственный след того,
    #: что часть объектов до расчёта не дошла. Ненулевое значение не делает
    #: результат неверным, но означает, что файл битый.
    unparsed_lines: int = 0

    def timing_point_at(self, time: float) -> TimingPoint:
        """ControlPointInfo.TimingPointAt: до первой точки действует первая же."""
        found = _point_at(self.timing_points, time)
        if found is not None:
            return found
        return self.timing_points[0] if self.timing_points else TimingPoint(time=0.0, beat_length=1000.0)

    def difficulty_point_at(self, time: float) -> DifficultyPoint:
        """LegacyControlPointInfo.DifficultyPointAt.

        Внимание: фолбэк здесь другой, чем у timing_point_at. До первой точки
        действует НЕ первая точка, а умолчание DifficultyControlPoint.DEFAULT
        со скоростью 1. Слайдер раньше первой зелёной линии получает именно её.
        """
        found = _point_at(self.difficulty_points, time)
        return found if found is not None else DifficultyPoint(time=0.0, slider_velocity=1.0)


def _point_at(points: list, time: float):
    """Последняя точка со временем <= time, либо None.

    ControlPointInfo делает бинарный поиск с EqualitySelection.Rightmost; здесь
    линейный проход. Для наших размеров карт разницы нет, а логика прозрачнее.
    """
    found = None
    for point in points:
        if point.time > time:
            break
        found = point
    return found


def _parse_float(value: str, limit: float = MAX_PARSE_VALUE, allow_nan: bool = False) -> float:
    """Parsing.ParseFloat/ParseDouble.

    Проверки повторяют C#: сначала предел, потом NaN. Числа за пределом osu!
    не зажимает, а отвергает вместе со всей картой.
    """
    number = float(value.strip())

    if number < -limit or number > limit:
        raise BeatmapParseError(f"value out of range ±{limit:g}: {value!r}")
    if not allow_nan and number != number:
        raise BeatmapParseError(f"value must not be NaN: {value!r}")

    return number


def _parse_int(value: str, limit: int = int(MAX_PARSE_VALUE)) -> int:
    """Parsing.ParseInt."""
    number = int(value.strip())

    if number < -limit or number > limit:
        raise BeatmapParseError(f"value out of range ±{limit}: {value!r}")

    return number


def decode_beatmap(path: str | Path) -> Beatmap:
    return decode_beatmap_string(_decode_text(Path(path).read_bytes()))


def _decode_text(raw: bytes) -> str:
    """StreamReader.DetectEncoding: osu! открывает файл с распознаванием BOM.

    Проверки и их порядок взяты оттуда же. Карты в UTF-16 в корпусе есть.
    """
    if raw.startswith(b"\xfe\xff"):
        return raw[2:].decode("utf-16-be", errors="replace")

    if raw.startswith(b"\xff\xfe"):
        # FF FE 00 00 — это UTF-32 LE, а не UTF-16 LE с нулевым первым символом.
        if raw[2:4] == b"\x00\x00":
            return raw[4:].decode("utf-32-le", errors="replace")
        return raw[2:].decode("utf-16-le", errors="replace")

    if raw.startswith(b"\x00\x00\xfe\xff"):
        return raw[4:].decode("utf-32-be", errors="replace")

    # Без метки StreamReader остаётся на своей кодировке по умолчанию, UTF-8.
    return raw.decode("utf-8-sig", errors="replace")


#: Исключения, которыми отвечает разбор одной строки. LegacyDecoder ловит на этом
#: месте вообще всё (`catch (Exception e)`), но шире этих трёх наш код не бросает.
_LINE_ERRORS = (BeatmapParseError, ValueError, IndexError)


def decode_beatmap_string(text: str) -> Beatmap:
    """Разбирает содержимое .osu. Любая причина отказа приходит одним типом."""
    try:
        return _decode(text)
    except BeatmapParseError:
        raise
    except (ValueError, IndexError) as exc:
        raise BeatmapParseError(f"cannot parse beatmap: {exc}") from exc


def _decode(text: str) -> Beatmap:
    """LegacyDecoder.ParseStreamInto.

    ВАЖНО: битая строка не отвергает карту, а пропускается — в C# каждый вызов
    ParseLine обёрнут в `try { ... } catch (Exception e) { Logger.Log(...) }`.
    Фатальны только две вещи, обе проверяются вне цикла: отсутствие строки
    формата и режим не osu!standard (последнее — уже наш охват, а не C#).
    """
    beatmap = Beatmap()
    section = ""
    saw_approach_rate = False
    saw_version = False
    hit_object_lines: list[str] = []
    timing_point_lines: list[str] = []

    for raw_line in text.splitlines():
        line = raw_line.strip()

        match = _VERSION_RE.match(line)
        if match:
            beatmap.format_version = int(match.group("version"))
            saw_version = True
            continue

        # Комментарии и пустые строки. В секции HitObjects "//" не встречается,
        # но LegacyDecoder отсекает их глобально.
        if not line or line.startswith("//"):
            continue

        match = _SECTION_RE.match(line)
        if match:
            section = match.group("name")
            continue

        try:
            if section == "General":
                key, _, value = line.partition(":")
                key = key.strip()
                if key == "Mode":
                    beatmap.mode = _parse_int(value)
                elif key == "StackLeniency":
                    # В C# поле объявлено как float, и порог стака считается им же.
                    beatmap.stack_leniency = f32(_parse_float(value))

            elif section == "Difficulty":
                key, _, value = line.partition(":")
                key = key.strip()
                if key == "HPDrainRate":
                    beatmap.drain_rate = f32(_parse_float(value))
                elif key == "CircleSize":
                    beatmap.circle_size = f32(_parse_float(value))
                elif key == "OverallDifficulty":
                    beatmap.overall_difficulty = f32(_parse_float(value))
                    if not saw_approach_rate:
                        beatmap.approach_rate = beatmap.overall_difficulty
                elif key == "ApproachRate":
                    beatmap.approach_rate = f32(_parse_float(value))
                    saw_approach_rate = True
                elif key == "SliderMultiplier":
                    beatmap.slider_multiplier = _parse_float(value)
                elif key == "SliderTickRate":
                    beatmap.slider_tick_rate = _parse_float(value)

            elif section == "Events":
                _parse_event(beatmap, line)

            elif section == "TimingPoints":
                timing_point_lines.append(line)

            elif section == "HitObjects":
                hit_object_lines.append(line)
        except _LINE_ERRORS:
            beatmap.unparsed_lines += 1

    if not saw_version:
        # Decoder.GetDecoder отвергает файл без строки формата.
        raise BeatmapParseError('no "osu file format vN" line found')

    if beatmap.mode != 0:
        raise BeatmapParseError(f"only osu!standard is supported, got Mode={beatmap.mode}")

    _apply_difficulty_restrictions(beatmap)
    _parse_timing_points(beatmap, timing_point_lines)

    beatmap.timing_points.sort(key=lambda p: p.time)
    beatmap.difficulty_points.sort(key=lambda p: p.time)

    for line in hit_object_lines:
        try:
            beatmap.hit_objects.append(_parse_hit_object(beatmap, line))
        except _LINE_ERRORS:
            beatmap.unparsed_lines += 1

    beatmap.hit_objects.sort(key=lambda h: h.start_time)
    return beatmap


#: LegacyEventType.Break. Enum.TryParse принимает и число, и имя.
_BREAK_EVENT_TYPES = ("2", "Break")


def _parse_event(beatmap: Beatmap, line: str) -> None:
    """LegacyBeatmapDecoder.handleEvent — из всей секции нужны только брейки."""
    parts = line.split(",")
    if parts[0] not in _BREAK_EVENT_TYPES:
        return
    if len(parts) < 3:
        raise BeatmapParseError(f"break line too short: {line!r}")

    start = _parse_float(parts[1])
    end = max(start, _parse_float(parts[2]))
    beatmap.breaks.append(BreakPeriod(start_time=start, end_time=end))


def _apply_difficulty_restrictions(beatmap: Beatmap) -> None:
    """LegacyBeatmapDecoder.applyDifficultyRestrictions.

    Настройки за пределами допустимого диапазона зажимаются, а не отвергаются:
    CS=12 в файле означает CS=10 в игре. Без этого расчёт на кривых картах
    расходится в разы, а не в младших разрядах.

    HP/CS/OD/AR в BeatmapDifficulty объявлены как float, поэтому границы точно
    представимы и повторного округления не требуется; множитель и частота тиков
    объявлены как double.
    """
    beatmap.drain_rate = min(max(beatmap.drain_rate, 0.0), 10.0)
    beatmap.circle_size = min(max(beatmap.circle_size, 0.0), 10.0)
    beatmap.overall_difficulty = min(max(beatmap.overall_difficulty, 0.0), 10.0)
    beatmap.approach_rate = min(max(beatmap.approach_rate, 0.0), 10.0)
    beatmap.slider_multiplier = min(max(beatmap.slider_multiplier, 0.4), 3.6)
    beatmap.slider_tick_rate = min(max(beatmap.slider_tick_rate, 0.5), 8.0)


@dataclass(frozen=True)
class _TimingLine:
    """Одна разобранная строка секции TimingPoints."""

    time: float
    beat_length: float
    slider_velocity: float
    generate_ticks: bool
    timing_change: bool


def _parse_timing_points(beatmap: Beatmap, lines: list[str]) -> None:
    """Разбор секции TimingPoints с группировкой одновременных строк.

    LegacyBeatmapDecoder копит точки, пока не сменится время, и лишь потом
    сбрасывает группу (addControlPoint/flushPendingPoints). Сброс идёт с конца
    списка, и для каждого типа побеждает первая встреченная точка. Красные
    строки при этом кладутся в начало списка, зелёные — в конец, из-за чего:

    * темп берётся из ПЕРВОЙ красной строки группы;
    * скорость слайдера — из ПОСЛЕДНЕЙ зелёной, и только при её отсутствии
      из первой красной.

    Порядок в файле здесь решает: зелёная линия побеждает красную с тем же
    временем, даже если записана раньше неё.
    """
    group: list[_TimingLine] = []

    def flush() -> None:
        if not group:
            return

        red = next((line for line in group if line.timing_change), None)
        if red is not None:
            beatmap.timing_points.append(TimingPoint(time=red.time, beat_length=red.beat_length))

        green = next((line for line in reversed(group) if not line.timing_change), None)
        # Если зелёных нет, вся группа красная, и первая её строка — та самая
        # первая красная, что нужна по правилу выше.
        source = green if green is not None else group[0]
        beatmap.difficulty_points.append(
            DifficultyPoint(
                time=source.time,
                slider_velocity=source.slider_velocity,
                generate_ticks=source.generate_ticks,
            )
        )
        group.clear()

    for line in lines:
        try:
            parsed = _parse_timing_line(beatmap, line)
        except _LINE_ERRORS:
            # Строка не дошла до addControlPoint, значит и накопленную группу
            # она не трогает: пропускаем, не сбрасывая её.
            beatmap.unparsed_lines += 1
            continue

        if group and parsed.time != group[0].time:
            flush()
        group.append(parsed)

    flush()


def _parse_timing_line(beatmap: Beatmap, line: str) -> _TimingLine:
    parts = line.split(",")
    if len(parts) < 2:
        raise BeatmapParseError(f"timing point line too short: {line!r}")

    time = _parse_float(parts[0])
    beat_length = _parse_float(parts[1], allow_nan=True)

    # Отрицательный beatLength кодирует множитель: -100 даёт 1.0x, -50 даёт 2.0x.
    # При NaN сравнение ложно и множитель остаётся единичным — как в C#.
    speed_multiplier = 100.0 / -beat_length if beat_length < 0 else 1.0

    # Признак смены темпа: 7-е поле. C# сравнивает первый символ БЕЗ обрезки
    # пробелов, поэтому " 1" означает зелёную линию, а не красную.
    timing_change = True
    if len(parts) > 6:
        if not parts[6]:
            raise BeatmapParseError(f"empty beat length field in line {line!r}")
        timing_change = parts[6][0] == "1"

    if timing_change and math.isnan(beat_length):
        raise BeatmapParseError("beatLength of a timing point must not be NaN")

    return _TimingLine(
        time=time,
        beat_length=beat_length,
        slider_velocity=speed_multiplier,
        generate_ticks=not math.isnan(beat_length),
        timing_change=timing_change,
    )


def _parse_hit_object(beatmap: Beatmap, line: str) -> HitObject:
    parts = line.split(",")
    if len(parts) < 4:
        raise BeatmapParseError(f"hit object line too short: {line!r}")

    position = _read_position(beatmap.format_version, parts[0], parts[1])
    start_time = _parse_float(parts[2])
    type_flags = _parse_int(parts[3])

    if type_flags & 1:
        return HitCircle(
            start_time=start_time,
            position=position,
        )

    if type_flags & 2:
        return _parse_slider(beatmap, parts, position, start_time)

    if type_flags & 8:
        end_time = _parse_float(parts[5]) if len(parts) > 5 else start_time
        return Spinner(
            start_time=start_time,
            # Позиция берётся не из файла: спиннер всегда в центре поля.
            position=Vec2(SPINNER_POSITION_X, SPINNER_POSITION_Y),
            spinner_end_time=max(end_time, start_time),
        )

    raise BeatmapParseError(f"unknown hit object type {type_flags} in line {line!r}")


def _parse_slider(
    beatmap: Beatmap,
    parts: list[str],
    position: Vec2,
    start_time: float,
) -> Slider:
    if len(parts) < 6:
        raise BeatmapParseError("slider has no path description")

    control_points = _convert_path_string(beatmap.format_version, parts[5], position)

    repeat_count = 0
    if len(parts) > 6:
        repeat_count = _parse_int(parts[6])
        if repeat_count > MAX_REPEAT_COUNT:
            raise BeatmapParseError(f"too many slider repeats: {repeat_count}")
        # osu-stable считал первый пролёт повтором, хотя повторов ещё нет.
        repeat_count = max(0, repeat_count - 1)

    expected_distance: float | None = None
    if len(parts) > 7 and parts[7].strip():
        length = _parse_float(parts[7], MAX_COORDINATE_VALUE)
        # Отрицательная и нулевая длина встречаются на кривых картах;
        # osu! трактует их как «вывести длину из геометрии».
        expected_distance = length if length > 0 else None

    return Slider(
        start_time=start_time,
        position=position,
        control_points=control_points,
        repeat_count=repeat_count,
        expected_distance=expected_distance,
    )


def _convert_path_string(format_version: int, point_string: str, offset: Vec2) -> list[PathControlPoint]:
    """ConvertHitObjectParser.convertPathString.

    Строка вида "P|472:181|442:308" разбивается на сегменты: буква начинает
    новый сегмент, остальное — точки относительно начала слайдера.
    """
    pieces = point_string.split("|")

    points: list[Vec2] = []
    segments: list[tuple[PathType, int]] = []

    for piece in pieces:
        if not piece:
            continue
        if piece[0].isalpha():
            segments.append((_convert_path_type(piece), len(points)))
            # Первый сегмент дополняется нулевой точкой — позицией самого слайдера.
            if not points:
                points.append(Vec2(0.0, 0.0))
        else:
            points.append(_read_point(format_version, piece, offset))

    result: list[PathControlPoint] = []
    for i, (seg_type, start_index) in enumerate(segments):
        if i < len(segments) - 1:
            end_index = segments[i + 1][1]
            end_point = points[end_index] if end_index < len(points) else None
            result += _convert_points(format_version, seg_type, points[start_index:end_index], end_point)
        else:
            result += _convert_points(format_version, seg_type, points[start_index:], None)

    return result


def _read_position(format_version: int, x_str: str, y_str: str) -> Vec2:
    """Координаты объекта или точки пути.

    До лазерного формата координаты усекались до целых — и у самого объекта тоже,
    а не только у точек пути (ConvertHitObjectParser.Parse).

    Усекается float32, а не double: в C# написано `(int)Parsing.ParseFloat(...)`,
    а ParseFloat возвращает float. Разница видна на значениях ближе одного ulp
    к целому: 255.999995 округляется до 256.0f и усекается в 256, тогда как
    в double остаётся 255.999995 и усекается в 255.
    """
    x = f32(_parse_float(x_str, MAX_COORDINATE_VALUE))
    y = f32(_parse_float(y_str, MAX_COORDINATE_VALUE))

    if format_version < FIRST_LAZER_VERSION:
        x = float(int(x))
        y = float(int(y))

    return Vec2(x, y)


def _read_point(format_version: int, value: str, start_pos: Vec2) -> Vec2:
    x_str, _, y_str = value.partition(":")
    return _read_position(format_version, x_str, y_str) - start_pos


def _convert_path_type(token: str) -> PathType:
    """ConvertHitObjectParser.convertPathType.

    "B" без числа — безье; "B3" и подобные — B-сплайн указанной степени.
    Неизвестная буква молча трактуется как Catmull, как и в C#.
    """
    match token[0]:
        case "B":
            if len(token) > 1:
                try:
                    degree = int(token[1:])
                except ValueError:
                    return BEZIER
                if degree > 0:
                    return PathType(SplineType.BSPLINE, degree)
            return BEZIER
        case "L":
            return LINEAR
        case "P":
            return PERFECT_CURVE
        case _:
            return CATMULL


def _convert_points(
    format_version: int,
    seg_type: PathType,
    points: list[Vec2],
    end_point: Vec2 | None,
) -> list[PathControlPoint]:
    """ConvertHitObjectParser.convertPoints.

    Здесь живут два краевых правила stable: вырождение дуги и неявные сегменты
    при совпадающих подряд точках.
    """
    vertices = [PathControlPoint(position=p) for p in points]

    if seg_type == PERFECT_CURVE:
        end_point_length = 0 if end_point is None else 1

        if format_version < FIRST_LAZER_VERSION:
            if len(vertices) + end_point_length != 3:
                seg_type = BEZIER
            else:
                third = end_point if end_point is not None else points[2]
                if _is_linear(points[0], points[1], third):
                    # stable схлопывал коллинеарную дугу в прямую.
                    seg_type = LINEAR
        elif len(vertices) + end_point_length > 3:
            seg_type = BEZIER

    if not vertices:
        # C# пишет `vertices[0].Type = type` без проверки и падает с
        # IndexOutOfRange, а ParseStreamInto теряет всю строку. Быть
        # снисходительнее нельзя: осу такой слайдер не считает, и комбо меняется.
        # Пустой сегмент даёт путь вида "D|I|C|K|S|B|82:226|..." — буквенные
        # токены подряд, каждый начинает сегмент с той же позиции.
        raise BeatmapParseError("empty path segment: consecutive types with no points")

    # Тип обязателен у первой точки сегмента.
    vertices[0].type = seg_type

    result: list[PathControlPoint] = []
    start_index = 0
    end_index = 0

    while True:
        end_index += 1
        if end_index >= len(vertices):
            break

        if vertices[end_index].position != vertices[end_index - 1].position:
            continue

        # Legacy-Catmull не поддерживает несколько сегментов: соседние сливаются.
        if seg_type == CATMULL and end_index > 1 and format_version < FIRST_LAZER_VERSION:
            continue

        # Последняя точка сегмента не может начать новый неявный сегмент.
        if end_index == len(vertices) - 1:
            continue

        vertices[end_index - 1].type = seg_type
        result += vertices[start_index:end_index]
        start_index = end_index + 1

    if start_index < end_index:
        result += vertices[start_index:end_index]

    return result


def _is_linear(p0: Vec2, p1: Vec2, p2: Vec2) -> bool:
    """Проверка коллинеарности через векторное произведение.

    Считается во float32: в C# это операции над компонентами Vector2.
    """
    cross = f32(f32(f32(p1.y - p0.y) * f32(p2.x - p0.x)) - f32(f32(p1.x - p0.x) * f32(p2.y - p0.y)))
    return math.fabs(cross) < FLOAT_EPSILON
