// Дамп-хук для сверки Python-порта с эталоном.
//
// Запиненный клон ppy/osu не патчится: OsuDifficultyCalculator не запечатан,
// а CreateDifficultyHitObjects и CreateDifficultyAttributes — protected override,
// поэтому подкласс перехватывает и объекты, и скиллы, и играбельную карту.
//
// Использование:
//   dotnet run --project oracle/dump -- <карта.osu> [-m HD -m DT] [-o out.json]

using System.Reflection;
using System.Text.Json;
using System.Text.Json.Serialization;
using osu.Game.Beatmaps;
using osu.Game.Beatmaps.Formats;
using osu.Game.Rulesets;
using osu.Game.Rulesets.Difficulty;
using osu.Game.Rulesets.Difficulty.Preprocessing;
using osu.Game.Rulesets.Difficulty.Skills;
using osu.Game.Rulesets.Mods;
using osu.Game.Rulesets.Objects.Types;
using osu.Game.Rulesets.Osu;
using osu.Game.Rulesets.Osu.Difficulty;
using osu.Game.Rulesets.Osu.Difficulty.Evaluators;
using osu.Game.Rulesets.Osu.Difficulty.Evaluators.Aim;
using osu.Game.Rulesets.Osu.Difficulty.Evaluators.Speed;
using osu.Game.Rulesets.Osu.Difficulty.Preprocessing;
using osu.Game.Rulesets.Osu.Difficulty.Skills;
using osu.Game.Rulesets.Osu.Mods;
using osu.Game.Rulesets.Osu.Objects;
using osu.Game.Utils;
using PerformanceCalculator;

namespace Dump
{
    /// <summary>
    /// Перехватывает промежуточное состояние расчёта, не меняя саму логику:
    /// оба метода вызывают base и лишь запоминают результат.
    /// </summary>
    public class DumpingOsuDifficultyCalculator : OsuDifficultyCalculator
    {
        public List<DifficultyHitObject> CapturedObjects { get; private set; } = new();
        public Skill[] CapturedSkills { get; private set; } = Array.Empty<Skill>();
        public IBeatmap? CapturedBeatmap { get; private set; }

        public DumpingOsuDifficultyCalculator(IRulesetInfo ruleset, IWorkingBeatmap beatmap)
            : base(ruleset, beatmap)
        {
        }

        protected override IEnumerable<DifficultyHitObject> CreateDifficultyHitObjects(IBeatmap beatmap, Mod[] mods)
        {
            // Материализуем список: base отдаёт уже готовый List, но контракт этого не гарантирует.
            CapturedObjects = base.CreateDifficultyHitObjects(beatmap, mods).ToList();
            return CapturedObjects;
        }

        protected override DifficultyAttributes CreateDifficultyAttributes(IBeatmap beatmap, Mod[] mods, Skill[] skills)
        {
            CapturedBeatmap = beatmap;
            CapturedSkills = skills;
            return base.CreateDifficultyAttributes(beatmap, mods, skills);
        }
    }

    public static class Program
    {
        public static int Main(string[] args)
        {
            string? beatmapPath = null;
            string? outPath = null;
            var modAcronyms = new List<string>();

            for (int i = 0; i < args.Length; i++)
            {
                switch (args[i])
                {
                    case "-m" or "--mod":
                        modAcronyms.Add(args[++i]);
                        break;

                    case "-o" or "--out":
                        outPath = args[++i];
                        break;

                    default:
                        beatmapPath = args[i];
                        break;
                }
            }

            if (beatmapPath == null)
            {
                Console.Error.WriteLine("использование: Dump <карта.osu> [-m МОД ...] [-o out.json]");
                return 2;
            }

            // ОБЯЗАТЕЛЬНО: osu-tools делает это в своём Program.cs. Декодер для
            // расчёта сложности отличается тем, что ApplyOffsets = false, то есть
            // карты формата ниже v5 НЕ сдвигаются на 24 мс. Без регистрации дамп
            // расходился с simulate по flashlight_difficulty: это единственный
            // скилл с секциями, привязанными к абсолютному времени.
            LegacyDifficultyCalculatorBeatmapDecoder.Register();

            var ruleset = new OsuRuleset();

            var mods = resolveMods(ruleset, modAcronyms);
            if (mods == null)
                return 2;

            var working = ProcessorWorkingBeatmap.FromFileOrId(beatmapPath);

            var calculator = new DumpingOsuDifficultyCalculator(ruleset.RulesetInfo, working);

            var attributes = (OsuDifficultyAttributes)calculator.Calculate(mods);
            var beatmap = calculator.CapturedBeatmap!;

            var payload = new
            {
                meta = new
                {
                    diffcalc_version = calculator.Version,
                    beatmap = Path.GetFileName(beatmapPath),
                    mods = mods.Select(m => m.Acronym).ToArray(),
                    clock_rate = ModUtils.CalculateRateWithMods(mods),
                },
                beatmap = beatmap.HitObjects.Cast<OsuHitObject>().Select(dumpHitObject).ToArray(),
                difficulty_objects = calculator.CapturedObjects.Cast<OsuDifficultyHitObject>().Select(dumpDifficultyObject).ToArray(),
                // Все эвалуаторы объявлены public static, поэтому их можно вызвать
                // напрямую и получить пообъектные значения до накопления страйна.
                // Это единственный способ локализовать расхождение внутри скилла.
                evaluators = calculator.CapturedObjects.Cast<OsuDifficultyHitObject>()
                    .Select(o => dumpEvaluators(o, mods)).ToArray(),
                skills = calculator.CapturedSkills.Select(dumpSkill).ToArray(),
                attributes = dumpAttributes(attributes),
                // Легаси-скоринг: брейки и производная от них длина «слива».
                // Секцию [Events] порт до сих пор не разбирал, и без прямой
                // сверки ошибка в ней проявилась бы только через peppy stars —
                // целое число, которое чаще всего её проглотит.
                legacy = dumpLegacy(working.Beatmap),
            };

            static object dumpLegacy(IBeatmap baseBeatmap)
            {
                int breakLength = baseBeatmap.Breaks
                    .Select(b => (int)Math.Round(b.EndTime) - (int)Math.Round(b.StartTime)).Sum();
                int drainLength = 0;

                if (baseBeatmap.HitObjects.Count > 0)
                {
                    drainLength = ((int)Math.Round(baseBeatmap.HitObjects[^1].StartTime)
                                   - (int)Math.Round(baseBeatmap.HitObjects[0].StartTime) - breakLength) / 1000;
                }

                return new
                {
                    breaks = baseBeatmap.Breaks
                        .Select(b => new { start_time = b.StartTime, end_time = b.EndTime }).ToArray(),
                    break_length = breakLength,
                    drain_length = drainLength,
                    object_count = baseBeatmap.HitObjects.Count,
                };
            }

            var options = new JsonSerializerOptions
            {
                WriteIndented = true,
                DefaultIgnoreCondition = JsonIgnoreCondition.Never,
                // На карте nan-slider часть промежуточных величин легитимно равна NaN,
                // и итоговые звёзды при этом остаются осмысленными. Порт обязан
                // воспроизвести это поведение, а не «починить» его, поэтому пишем
                // NaN/Infinity как есть (строками), а не подменяем на null.
                NumberHandling = JsonNumberHandling.AllowNamedFloatingPointLiterals,
            };
            string json = JsonSerializer.Serialize(payload, options);

            if (outPath != null)
            {
                File.WriteAllText(outPath, json);
                Console.Error.WriteLine($"записано: {outPath} ({json.Length:N0} байт)");
            }
            else
                Console.WriteLine(json);

            return 0;
        }

        private static Mod[]? resolveMods(OsuRuleset ruleset, List<string> acronyms)
        {
            var available = ruleset.CreateAllMods().ToArray();
            var resolved = new List<Mod>();

            foreach (string acronym in acronyms)
            {
                var match = available.FirstOrDefault(m => string.Equals(m.Acronym, acronym, StringComparison.OrdinalIgnoreCase));
                if (match == null)
                {
                    Console.Error.WriteLine($"неизвестный мод: {acronym}");
                    return null;
                }

                resolved.Add(match);
            }

            return resolved.ToArray();
        }

        private static object dumpHitObject(OsuHitObject h, int index)
        {
            var result = new Dictionary<string, object?>
            {
                ["index"] = index,
                ["type"] = h.GetType().Name,
                ["start_time"] = h.StartTime,
                ["x"] = h.Position.X,
                ["y"] = h.Position.Y,
                ["stacked_x"] = h.StackedPosition.X,
                ["stacked_y"] = h.StackedPosition.Y,
                ["stack_height"] = h.StackHeight,
                ["radius"] = h.Radius,
                ["scale"] = h.Scale,
                ["time_preempt"] = h.TimePreempt,
                ["time_fade_in"] = h.TimeFadeIn,
                ["new_combo"] = h.NewCombo,
            };

            if (h is Slider slider)
            {
                result["repeat_count"] = slider.RepeatCount;
                result["span_count"] = slider.SpanCount();
                result["span_duration"] = slider.SpanDuration;
                result["duration"] = slider.Duration;
                result["end_time"] = slider.EndTime;
                result["velocity"] = slider.Velocity;
                result["tick_distance"] = slider.TickDistance;
                result["path_distance"] = slider.Path.Distance;
                result["path_expected_distance"] = slider.Path.ExpectedDistance.Value;
                result["path_control_points"] = slider.Path.ControlPoints
                    // Degree обязателен: SplineType не различает безье и B-сплайн — безье это
                    // BSpline с Degree = null. Без этого поля дамп теряет вид кривой.
                    .Select(p => new { x = p.Position.X, y = p.Position.Y, type = p.Type?.Type.ToString(), degree = p.Type?.Degree })
                    .ToArray();
                result["nested"] = slider.NestedHitObjects.Cast<OsuHitObject>()
                    .Select(n => new
                    {
                        type = n.GetType().Name,
                        start_time = n.StartTime,
                        x = n.Position.X,
                        y = n.Position.Y,
                        stacked_x = n.StackedPosition.X,
                        stacked_y = n.StackedPosition.Y,
                    })
                    .ToArray();
            }
            else if (h is Spinner spinner)
            {
                result["duration"] = spinner.Duration;
                result["end_time"] = spinner.EndTime;
            }

            return result;
        }

        private static object dumpDifficultyObject(OsuDifficultyHitObject o, int index)
        {
            return new Dictionary<string, object?>
            {
                ["index"] = index,
                ["base_type"] = o.BaseObject.GetType().Name,
                ["start_time"] = o.StartTime,
                ["end_time"] = o.EndTime,
                ["delta_time"] = o.DeltaTime,
                // В ребалансе 2026 StrainTime переименован в AdjustedDeltaTime.
                // rosu-pp всё ещё зовёт это strain_time — удобный маркер расхождения версий.
                ["adjusted_delta_time"] = o.AdjustedDeltaTime,
                ["last_object_end_delta_time"] = o.LastObjectEndDeltaTime,
                ["preempt"] = o.Preempt,
                ["jump_distance"] = o.JumpDistance,
                ["lazy_jump_distance"] = o.LazyJumpDistance,
                ["minimum_jump_distance"] = o.MinimumJumpDistance,
                ["minimum_jump_time"] = o.MinimumJumpTime,
                ["travel_distance"] = o.TravelDistance,
                ["travel_time"] = o.TravelTime,
                ["lazy_travel_distance"] = o.LazyTravelDistance,
                ["lazy_travel_time"] = o.LazyTravelTime,
                ["lazy_end_x"] = o.LazyEndPosition?.X,
                ["lazy_end_y"] = o.LazyEndPosition?.Y,
                ["angle"] = o.Angle,
                ["normalised_vector_angle"] = o.NormalisedVectorAngle,
                ["small_circle_bonus"] = o.SmallCircleBonus,
                ["overall_difficulty"] = o.OverallDifficulty,
                ["hit_window_great"] = o.HitWindowGreat,
                // Прозрачность на момент клика — вход скилла Reading, обе ветки HD.
                ["opacity_at_click"] = o.OpacityAt(o.BaseObject.StartTime, false),
                ["opacity_at_click_hidden"] = o.OpacityAt(o.BaseObject.StartTime, true),
            };
        }

        private static object dumpEvaluators(OsuDifficultyHitObject o, Mod[] mods)
        {
            bool hidden = mods.OfType<OsuModHidden>().Any(m => !m.OnlyFadeApproachCircles.Value);

            return new Dictionary<string, object?>
            {
                ["reading"] = ReadingEvaluator.EvaluateDifficultyOf(o, hidden),
                ["speed"] = SpeedEvaluator.EvaluateDifficultyOf(o),
                ["rhythm"] = RhythmEvaluator.EvaluateDifficultyOf(o),
                ["flashlight"] = FlashlightEvaluator.EvaluateDifficultyOf(o, mods),
                ["agility"] = AgilityEvaluator.EvaluateDifficultyOf(o),
                ["snap_aim"] = SnapAimEvaluator.EvaluateDifficultyOf(o, true),
                ["snap_aim_no_sliders"] = SnapAimEvaluator.EvaluateDifficultyOf(o, false),
                ["flow_aim"] = FlowAimEvaluator.EvaluateDifficultyOf(o, true),
                ["flow_aim_no_sliders"] = FlowAimEvaluator.EvaluateDifficultyOf(o, false),
            };
        }

        private static object dumpSkill(Skill skill)
        {
            var result = new Dictionary<string, object?>
            {
                ["name"] = skill.GetType().Name,
                ["difficulty_value"] = skill.DifficultyValue(),
            };

            // Aim создаётся дважды — со слайдерами и без; различаем их явно.
            if (skill is Aim aim)
                result["include_sliders"] = aim.IncludeSliders;

            // Пообъектные сложности лежат в protected-члене. Рефлексия здесь оправдана:
            // это дев-инструмент, а альтернатива — дублировать логику CreateSkills,
            // которая ломается на каждом ребалансе.
            result["object_difficulties"] = readObjectDifficulties(skill);

            // Пики страйнов есть не у всех: Speed и Reading — HarmonicSkill, страйнов у них нет.
            // ВАЖНО: GetCurrentStrainPeaks() мутирует состояние (запечатывает последнюю секцию),
            // поэтому вызывается только здесь, после завершения Calculate().
            switch (skill)
            {
                case VariableLengthStrainSkill variable:
                    result["strain_peaks"] = variable.GetCurrentStrainPeaks()
                        .Select(p => new { value = p.Value, section_length = p.SectionLength })
                        .ToArray();
                    break;

                case StrainSkill strain:
                    result["strain_peaks"] = strain.GetCurrentStrainPeaks().ToArray();
                    break;
            }

            return result;
        }

        private static double[]? readObjectDifficulties(Skill skill)
        {
            for (var type = skill.GetType(); type != null; type = type.BaseType)
            {
                const BindingFlags flags = BindingFlags.Instance | BindingFlags.Public | BindingFlags.NonPublic | BindingFlags.DeclaredOnly;

                object? raw = type.GetProperty("ObjectDifficulties", flags)?.GetValue(skill)
                              ?? type.GetField("ObjectDifficulties", flags)?.GetValue(skill);

                if (raw is IEnumerable<double> difficulties)
                    return difficulties.ToArray();
            }

            return null;
        }

        private static object dumpAttributes(OsuDifficultyAttributes a) => new Dictionary<string, object?>
        {
            ["star_rating"] = a.StarRating,
            ["max_combo"] = a.MaxCombo,
            ["aim_difficulty"] = a.AimDifficulty,
            ["aim_difficult_slider_count"] = a.AimDifficultSliderCount,
            ["speed_difficulty"] = a.SpeedDifficulty,
            ["speed_note_count"] = a.SpeedNoteCount,
            ["flashlight_difficulty"] = a.FlashlightDifficulty,
            ["reading_difficulty"] = a.ReadingDifficulty,
            ["slider_factor"] = a.SliderFactor,
            ["aim_difficult_strain_count"] = a.AimDifficultStrainCount,
            ["speed_difficult_strain_count"] = a.SpeedDifficultStrainCount,
            ["reading_difficult_note_count"] = a.ReadingDifficultNoteCount,
            ["aim_top_weighted_slider_factor"] = a.AimTopWeightedSliderFactor,
            ["speed_top_weighted_slider_factor"] = a.SpeedTopWeightedSliderFactor,
            ["hit_circle_count"] = a.HitCircleCount,
            ["slider_count"] = a.SliderCount,
            ["spinner_count"] = a.SpinnerCount,
        };
    }
}
