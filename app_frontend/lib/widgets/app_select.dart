import 'package:flutter/material.dart';
import 'package:lucide_icons_flutter/lucide_icons.dart';

import '../theme/tokens.dart';
import 'app_input.dart';

class SelectOption {
  const SelectOption({required this.value, required this.label});

  final String value;
  final String label;
}

/// Options under a heading.
///
/// The web app renders these as `<optgroup>`, which exists because a flat list of
/// sixty categories is unusable - "Household & Personal" as a heading is the
/// difference between scanning and hunting.
class SelectGroup {
  const SelectGroup({required this.label, required this.options});

  final String label;
  final List<SelectOption> options;
}

/// A labelled picker, matching [AppInput]'s metrics.
///
/// Built on `PopupMenuButton` rather than `DropdownButton`, and the reason is
/// grouping: `DropdownButton` has no notion of a non-selectable heading, so the
/// eighty-category Billing picker would have to be flattened. A popup menu can carry
/// disabled heading rows, which is exactly what an `<optgroup>` is.
///
/// The field itself is `rounded-lg border px-3 py-2 text-[13px]`, which is the web
/// app's `Select` - deliberately a little different from its `Input`, and reproduced
/// rather than harmonised so a form that mixes them looks the same on both surfaces.
class AppSelect extends StatelessWidget {
  const AppSelect({
    super.key,
    this.label,
    required this.value,
    required this.onChanged,
    this.options,
    this.groups,
    this.placeholder,
    this.hint,
    this.error,
    this.action,
    this.required = false,
    this.enabled = true,
    this.width,
  }) : assert(
         options != null || groups != null,
         'A select needs options or groups',
       );

  final String? label;

  /// The selected value. Empty means nothing chosen, which pairs with [placeholder].
  final String value;
  final ValueChanged<String> onChanged;

  /// Flat options. Ignored when [groups] is given.
  final List<SelectOption>? options;

  /// Grouped options, rendered under headings.
  final List<SelectGroup>? groups;

  /// Leading entry for "nothing chosen". Omit when a value is always required.
  final String? placeholder;

  final String? hint;
  final String? error;

  /// Rendered to the right of the label - an "add new" affordance, usually.
  final Widget? action;

  final bool required;
  final bool enabled;
  final double? width;

  /// How tall the menu may grow: about eight rows, and never a large fraction of the
  /// window.
  ///
  /// **`PopupMenuButton` does not shrink a menu that will not fit below its button - it
  /// slides the whole menu up until it does.** A generous cap is therefore not a maximum
  /// height, it is a licence to move: the audit log's forty-odd actions asked for 420px
  /// under a field sitting near the top of a short window, could not have it, and opened
  /// as a panel over the page title instead - while the four-item severity filter beside
  /// it, needing only ~160px, stayed neatly under its field.
  ///
  /// So the fix is to make the long menu ask for about what the short one asks for.
  /// Eight rows is the conventional depth for a select: enough to scan, small enough
  /// that it fits beneath the field in any realistic layout, and the rest scrolls.
  ///
  /// A field genuinely in the bottom third of the window will still open upwards. That
  /// is the correct answer there - the alternative is a menu running off-screen - and it
  /// is no longer the answer for a field near the top, which was the actual complaint.
  double _menuMaxHeight(BuildContext context) =>
      (MediaQuery.sizeOf(context).height * 0.4).clamp(200.0, 300.0);

  List<SelectOption> get _flat => groups != null
      ? groups!
            .expand((SelectGroup group) => group.options)
            .toList(growable: false)
      : (options ?? const <SelectOption>[]);

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;

    final SelectOption? selected = _flat
        .where((SelectOption option) => option.value == value)
        .firstOrNull;
    final String display = selected?.label ?? placeholder ?? '';
    final bool isPlaceholder = selected == null;

    final bool hasError = error != null && error!.trim().isNotEmpty;
    final bool errorState = error != null;

    final Widget field = PopupMenuButton<String>(
      enabled: enabled && (_flat.isNotEmpty || placeholder != null),
      tooltip: '',
      // Anchored under the field and matched to its width, so it reads as the field
      // expanding rather than as a menu appearing somewhere near it.
      position: PopupMenuPosition.under,
      constraints: BoxConstraints(
        minWidth: 220,
        maxHeight: _menuMaxHeight(context),
      ),
      // 120 ms in and out, on the curve everything else in this app moves on.
      //
      // Material's default is a 300 ms *height* animation - the panel unrolls to its
      // full length rather than appearing - which next to this app's 120 ms hovers and
      // presses reads as the menu labouring into place. A list of forty audit actions
      // unrolling over a third of a second is the worst case of it.
      popUpAnimationStyle: AnimationStyle(
        duration: Motion.fast,
        reverseDuration: Motion.fast,
        curve: Motion.easeOutQuart,
      ),
      onSelected: onChanged,
      itemBuilder: (BuildContext context) => <PopupMenuEntry<String>>[
        if (placeholder != null)
          PopupMenuItem<String>(
            value: '',
            height: 34,
            child: Text(
              placeholder!,
              style: TextStyle(fontSize: 13, color: t.contentMuted),
            ),
          ),
        if (groups != null)
          for (final SelectGroup group in groups!.where(
            (SelectGroup group) => group.options.isNotEmpty,
          )) ...<PopupMenuEntry<String>>[
            PopupMenuItem<String>(
              // The heading is a row that cannot be chosen - an `<optgroup>` label.
              enabled: false,
              height: 30,
              child: Text(
                group.label.toUpperCase(),
                style: TextStyle(
                  fontSize: 10,
                  fontWeight: FontWeight.w600,
                  letterSpacing: 0.6,
                  color: t.contentMuted,
                ),
              ),
            ),
            for (final SelectOption option in group.options)
              _optionItem(t, option, indented: true),
          ]
        else
          for (final SelectOption option in options ?? const <SelectOption>[])
            _optionItem(t, option),
      ],
      child: Container(
        padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 8),
        decoration: BoxDecoration(
          color: enabled ? t.surface : t.surfaceSunken,
          borderRadius: BorderRadius.circular(Radii.lg),
          border: Border.all(color: errorState ? t.danger : t.border),
        ),
        child: Row(
          children: <Widget>[
            Expanded(
              child: Text(
                display,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 13,
                  height: 1.35,
                  color: isPlaceholder ? t.contentMuted : t.content,
                ),
              ),
            ),
            const SizedBox(width: 6),
            // A picker with no indicator looks like a text field, so the chevron is
            // not optional.
            Icon(LucideIcons.chevronDown, size: 14, color: t.contentMuted),
          ],
        ),
      ),
    );

    final Widget content = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        if (label != null) ...<Widget>[
          FieldLabel(label: label!, required: required, action: action),
          const SizedBox(height: 6),
        ],
        field,
        if (hasError) ...<Widget>[
          const SizedBox(height: 6),
          Semantics(
            liveRegion: true,
            child: Text(
              error!,
              style: TextStyle(fontSize: 12, color: t.danger),
            ),
          ),
        ] else if (hint != null) ...<Widget>[
          const SizedBox(height: 6),
          Text(hint!, style: TextStyle(fontSize: 12, color: t.contentMuted)),
        ],
      ],
    );

    return width == null ? content : SizedBox(width: width, child: content);
  }

  PopupMenuItem<String> _optionItem(
    AppTokens t,
    SelectOption option, {
    bool indented = false,
  }) {
    final bool isSelected = option.value == value;
    return PopupMenuItem<String>(
      value: option.value,
      height: 34,
      child: Padding(
        padding: EdgeInsets.only(left: indented ? 8 : 0),
        child: Row(
          children: <Widget>[
            Expanded(
              child: Text(
                option.label,
                overflow: TextOverflow.ellipsis,
                style: TextStyle(
                  fontSize: 13,
                  color: isSelected ? t.primary : t.contentSecondary,
                  fontWeight: isSelected ? FontWeight.w500 : FontWeight.w400,
                ),
              ),
            ),
            if (isSelected) Icon(LucideIcons.check, size: 13, color: t.primary),
          ],
        ),
      ),
    );
  }
}
