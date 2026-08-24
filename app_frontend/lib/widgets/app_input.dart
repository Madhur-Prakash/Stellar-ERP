import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../core/decimal_input.dart';
import '../theme/oklch.dart';
import '../theme/tokens.dart';

/// A labelled text field.
///
/// The wiring is why this is a component and not a styled `TextField`: the label,
/// the error, the hint, and the accessible relationship between them. Repeating that
/// at every call site is how forms end up unusable with a screen reader.
///
/// Two details carried over deliberately from `components/ui/Input.tsx`:
///
/// * **An error replaces the hint rather than joining it.** Two lines of small text
///   under a 36px field pushes the next field down and makes the form jump as
///   validation fires.
/// * **The error is announced, not merely shown.** `liveRegion` is Flutter's
///   `role="alert"`: without it a screen-reader user finds the message only by
///   navigating onto it, which is after they have already tried to submit.
class AppInput extends StatefulWidget {
  const AppInput({
    super.key,
    this.label,
    this.controller,
    this.initialValue,
    this.onChanged,
    this.onSubmitted,
    this.hint,
    this.error,
    this.placeholder,
    this.leftIcon,
    this.rightSlot,
    this.obscureText = false,
    this.enabled = true,
    this.required = false,
    this.autofocus = false,
    this.keyboardType,
    this.inputFormatters,
    this.maxLength,
    this.textAlign = TextAlign.start,
    this.textStyle,
    this.width,
    this.focusNode,
    this.maxLines = 1,
    this.autofillHints,
  });

  final String? label;
  final TextEditingController? controller;
  final String? initialValue;
  final ValueChanged<String>? onChanged;
  final ValueChanged<String>? onSubmitted;

  /// Guidance below the field, hidden while an error is shown.
  final String? hint;

  /// Validation message. Its presence switches the field into the error state.
  final String? error;

  final String? placeholder;
  final IconData? leftIcon;

  /// Rendered inside the field on the right - the password reveal toggle.
  final Widget? rightSlot;

  final bool obscureText;
  final bool enabled;
  final bool required;
  final bool autofocus;
  final TextInputType? keyboardType;
  final List<TextInputFormatter>? inputFormatters;
  final int? maxLength;
  final TextAlign textAlign;
  final TextStyle? textStyle;
  final double? width;
  final FocusNode? focusNode;
  final int maxLines;
  final List<String>? autofillHints;

  @override
  State<AppInput> createState() => _AppInputState();
}

class _AppInputState extends State<AppInput> {
  TextEditingController? _internal;
  FocusNode? _internalFocus;
  bool _focused = false;

  TextEditingController get _controller =>
      widget.controller ??
      (_internal ??= TextEditingController(text: widget.initialValue));

  FocusNode get _focus => widget.focusNode ?? (_internalFocus ??= FocusNode());

  @override
  void initState() {
    super.initState();
    _focus.addListener(_onFocusChange);
  }

  void _onFocusChange() {
    if (!mounted) return;
    setState(() => _focused = _focus.hasFocus);
  }

  @override
  void dispose() {
    _focus.removeListener(_onFocusChange);
    _internalFocus?.dispose();
    _internal?.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final bool hasError =
        widget.error != null && widget.error!.trim().isNotEmpty;
    // A single space is the web app's way of turning the error state on without
    // printing a message - the Billing category picker uses it. Honoured here.
    final bool errorStateOnly =
        widget.error != null && widget.error!.trim().isEmpty;

    final Color borderColour = hasError || errorStateOnly
        ? t.danger
        : _focused
        ? t.primary
        : t.border;

    final Widget field = Container(
      constraints: BoxConstraints(minHeight: widget.maxLines > 1 ? 72 : 36),
      decoration: BoxDecoration(
        color: widget.enabled ? t.surface : t.surfaceSunken,
        borderRadius: BorderRadius.circular(Radii.md),
        border: Border.all(color: borderColour, width: _focused ? 2 : 1),
        // `focus:ring-2 ring-ring/25` - an outer glow rather than a thicker border,
        // so the field does not change size when focused.
        boxShadow: _focused
            ? <BoxShadow>[
                BoxShadow(
                  color: (hasError ? t.danger : t.ring).at(0.25),
                  blurRadius: 0,
                  spreadRadius: 2,
                ),
              ]
            : null,
      ),
      child: Row(
        crossAxisAlignment: widget.maxLines > 1
            ? CrossAxisAlignment.start
            : CrossAxisAlignment.center,
        children: <Widget>[
          if (widget.leftIcon != null)
            Padding(
              // `pl-3` to the glyph, `pl-9` to the text.
              padding: EdgeInsets.only(
                left: 11,
                right: 6,
                top: widget.maxLines > 1 ? 9 : 0,
              ),
              child: Icon(widget.leftIcon, size: 16, color: t.contentMuted),
            ),
          Expanded(
            child: Padding(
              padding: EdgeInsets.only(
                left: widget.leftIcon != null ? 0 : 11,
                right: widget.rightSlot != null ? 0 : 11,
              ),
              child: TextField(
                controller: _controller,
                focusNode: _focus,
                enabled: widget.enabled,
                autofocus: widget.autofocus,
                obscureText: widget.obscureText,
                keyboardType: widget.keyboardType,
                inputFormatters: widget.inputFormatters,
                maxLength: widget.maxLength,
                maxLines: widget.maxLines,
                textAlign: widget.textAlign,
                autofillHints: widget.autofillHints,
                onChanged: widget.onChanged,
                onSubmitted: widget.onSubmitted,
                style: (widget.textStyle ?? const TextStyle()).copyWith(
                  fontSize: widget.textStyle?.fontSize ?? 14,
                  color: widget.enabled ? t.content : t.contentMuted,
                  height: 1.3,
                ),
                cursorColor: t.primary,
                decoration: InputDecoration(
                  isDense: true,
                  filled: false,
                  border: InputBorder.none,
                  enabledBorder: InputBorder.none,
                  focusedBorder: InputBorder.none,
                  disabledBorder: InputBorder.none,
                  errorBorder: InputBorder.none,
                  focusedErrorBorder: InputBorder.none,
                  // Suppressed: Flutter's counter adds a line of text under the
                  // field, which no input in the web app has.
                  counterText: '',
                  contentPadding: EdgeInsets.symmetric(
                    vertical: widget.maxLines > 1 ? 9 : 8,
                  ),
                  hintText: widget.placeholder,
                  hintStyle: TextStyle(
                    fontSize: widget.textStyle?.fontSize ?? 14,
                    color: t.contentMuted,
                    height: 1.3,
                  ),
                ),
              ),
            ),
          ),
          ?widget.rightSlot,
        ],
      ),
    );

    final Widget content = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        if (widget.label != null) ...<Widget>[
          _FieldLabel(label: widget.label!, required: widget.required),
          const SizedBox(height: 6),
        ],
        field,
        if (hasError) ...<Widget>[
          const SizedBox(height: 6),
          Semantics(
            liveRegion: true,
            child: Text(
              widget.error!,
              style: TextStyle(fontSize: 13, color: t.danger, height: 1.35),
            ),
          ),
        ] else if (widget.hint != null) ...<Widget>[
          const SizedBox(height: 6),
          Text(
            widget.hint!,
            style: TextStyle(fontSize: 13, color: t.contentMuted, height: 1.35),
          ),
        ],
      ],
    );

    return widget.width == null
        ? content
        : SizedBox(width: widget.width, child: content);
  }
}

/// The label above a field, with the required marker.
///
/// Shared by [AppInput] and `AppSelect` so the two never drift - which they had, in
/// the web app, before `Select` was extracted: a row mixing them showed labels at
/// different baselines.
class _FieldLabel extends StatelessWidget {
  const _FieldLabel({required this.label, required this.required});

  final String label;
  final bool required;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    return Row(
      mainAxisSize: MainAxisSize.min,
      children: <Widget>[
        Text(
          label,
          style: TextStyle(
            fontSize: 13,
            fontWeight: FontWeight.w500,
            color: t.contentSecondary,
            height: 1.3,
          ),
        ),
        if (required)
          Text(
            '*',
            style: TextStyle(fontSize: 13, color: t.danger, height: 1.3),
          ),
      ],
    );
  }
}

/// The label row, for widgets outside this file that need to match it.
class FieldLabel extends StatelessWidget {
  const FieldLabel({
    super.key,
    required this.label,
    this.required = false,
    this.action,
  });

  final String label;
  final bool required;

  /// Rendered to the right of the label - an "add new" affordance, usually.
  final Widget? action;

  @override
  Widget build(BuildContext context) {
    if (action == null) return _FieldLabel(label: label, required: required);
    return Row(
      crossAxisAlignment: CrossAxisAlignment.baseline,
      textBaseline: TextBaseline.alphabetic,
      children: <Widget>[
        _FieldLabel(label: label, required: required),
        const Spacer(),
        action!,
      ],
    );
  }
}

/// An input that will only hold a number.
///
/// **Not a numeric keyboard type alone.** On a desktop with a physical keyboard that
/// changes nothing, so a letter typed into an amount field would be accepted and
/// then rejected by the server. Every keystroke, paste, and drag-drop goes through
/// [sanitiseDecimal] instead, so a letter simply never appears.
class AppNumberInput extends StatelessWidget {
  const AppNumberInput({
    super.key,
    this.label,
    this.controller,
    this.onChanged,
    this.hint,
    this.error,
    this.placeholder,
    this.decimals = 4,
    this.allowNegative = false,
    this.required = false,
    this.autofocus = false,
    this.enabled = true,
    this.width,
    this.focusNode,
    this.textStyle,
  });

  final String? label;
  final TextEditingController? controller;
  final ValueChanged<String>? onChanged;
  final String? hint;
  final String? error;
  final String? placeholder;
  final int decimals;
  final bool allowNegative;
  final bool required;
  final bool autofocus;
  final bool enabled;
  final double? width;
  final FocusNode? focusNode;
  final TextStyle? textStyle;

  @override
  Widget build(BuildContext context) {
    return AppInput(
      label: label,
      controller: controller,
      onChanged: onChanged,
      hint: hint,
      error: error,
      placeholder: placeholder,
      required: required,
      autofocus: autofocus,
      enabled: enabled,
      width: width,
      focusNode: focusNode,
      textStyle: textStyle,
      keyboardType: const TextInputType.numberWithOptions(decimal: true),
      inputFormatters: <TextInputFormatter>[
        DecimalInputFormatter(decimals: decimals, allowNegative: allowNegative),
      ],
    );
  }
}

/// A date field.
///
/// The web app uses `<input type="date">`, which is the browser's own picker. Flutter
/// has no such control, so this is a read-only field that opens Material's
/// `showDatePicker` - and it keeps the ISO string as its value, because that is what
/// the API takes and converting at the boundary is where off-by-one-day bugs live.
class AppDateInput extends StatelessWidget {
  const AppDateInput({
    super.key,
    this.label,
    required this.value,
    required this.onChanged,
    this.hint,
    this.error,
    this.maximum,
    this.minimum,
    this.width,
    this.enabled = true,
  });

  final String? label;

  /// `YYYY-MM-DD`, or empty for no date chosen.
  final String value;
  final ValueChanged<String> onChanged;
  final String? hint;
  final String? error;

  /// ISO bounds. Billing caps the date at today, because an entry cannot be
  /// recorded for a day that has not happened.
  final String? maximum;
  final String? minimum;

  final double? width;
  final bool enabled;

  @override
  Widget build(BuildContext context) {
    final AppTokens t = context.tokens;
    final DateTime? parsed = DateTime.tryParse(value);

    final Widget content = Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: <Widget>[
        if (label != null) ...<Widget>[
          FieldLabel(label: label!),
          const SizedBox(height: 6),
        ],
        Material(
          color: Colors.transparent,
          child: InkWell(
            onTap: enabled ? () => _pick(context, parsed) : null,
            borderRadius: BorderRadius.circular(Radii.md),
            child: Container(
              height: 36,
              padding: const EdgeInsets.symmetric(horizontal: 11),
              decoration: BoxDecoration(
                color: enabled ? t.surface : t.surfaceSunken,
                borderRadius: BorderRadius.circular(Radii.md),
                border: Border.all(color: error != null ? t.danger : t.border),
              ),
              child: Row(
                children: <Widget>[
                  Expanded(
                    child: Text(
                      value.isEmpty ? 'Choose a date' : value,
                      style: TextStyle(
                        fontSize: 14,
                        color: value.isEmpty ? t.contentMuted : t.content,
                      ),
                    ),
                  ),
                  Icon(
                    Icons.calendar_today_outlined,
                    size: 14,
                    color: t.contentMuted,
                  ),
                ],
              ),
            ),
          ),
        ),
        if (error != null) ...<Widget>[
          const SizedBox(height: 6),
          Text(error!, style: TextStyle(fontSize: 13, color: t.danger)),
        ] else if (hint != null) ...<Widget>[
          const SizedBox(height: 6),
          Text(hint!, style: TextStyle(fontSize: 13, color: t.contentMuted)),
        ],
      ],
    );

    return width == null ? content : SizedBox(width: width, child: content);
  }

  Future<void> _pick(BuildContext context, DateTime? current) async {
    final DateTime? chosen = await showDatePicker(
      context: context,
      initialDate: current ?? DateTime.now(),
      firstDate: DateTime.tryParse(minimum ?? '') ?? DateTime(2000),
      lastDate: DateTime.tryParse(maximum ?? '') ?? DateTime(2100),
    );
    if (chosen == null) return;
    final String month = chosen.month.toString().padLeft(2, '0');
    final String day = chosen.day.toString().padLeft(2, '0');
    onChanged('${chosen.year}-$month-$day');
  }
}
