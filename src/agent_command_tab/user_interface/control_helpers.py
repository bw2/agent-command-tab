"""Helpers for buttons and pop-ups shared by the list's rows, the panel around the list and the settings window."""

from AppKit import (NSEventModifierFlagCommand, NSEventModifierFlagControl, NSEventModifierFlagOption, NSFontWeightBold, NSImage,
                    NSImageOnly, NSImageSymbolConfiguration)


def is_a_command_press_of_letter(letter, characters, modifier_flags):
    """Whether a key press is Command and the letter, with or without Shift, and without Option or
    Control: Command-R and Command-Shift-R rename the selected row, in the list and in the Saved
    sessions window, and Command-E and Command-Shift-E expand or collapse all (user requests, 2026-11-07).

    Args:
        letter: The lowercase letter, such as "r".
        characters: The key event's charactersIgnoringModifiers, which Shift turns into the uppercase letter.
        modifier_flags: The key event's modifierFlags.
    """
    return (characters in (letter, letter.upper()) and bool(modifier_flags & NSEventModifierFlagCommand)
            and not modifier_flags & (NSEventModifierFlagOption | NSEventModifierFlagControl))


def show_symbol_on_button(button, symbol_name, fallback_title, accessibility_description, point_size=None):
    """Shows an SF Symbol on the button (at point_size, if given), or the fallback text where the
    symbol doesn't exist."""
    image = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol_name, accessibility_description)
    if image is not None and point_size is not None:
        image = image.imageWithSymbolConfiguration_(
            NSImageSymbolConfiguration.configurationWithPointSize_weight_(point_size, NSFontWeightBold))
    button.setImage_(image)
    button.setTitle_("" if image is not None else fallback_title)
    button.setImagePosition_(NSImageOnly if image is not None else 0)  # 0: NSNoImage, the title alone


def add_popup_choices(popup, titles_by_value):
    """Adds one item to an NSPopUpButton for each setting value, titled from titles_by_value,
    that keeps its value, so a choice is read back by value rather than by its title."""
    for value, title in titles_by_value.items():
        popup.addItemWithTitle_(title)
        popup.lastItem().setRepresentedObject_(value)


def select_popup_choice(popup, value):
    """Selects the pop-up's item for a setting value (add_popup_choices)."""
    popup.selectItemAtIndex_(popup.indexOfItemWithRepresentedObject_(value))


def read_popup_choice(popup):
    """Returns the setting value of the pop-up's selected item (add_popup_choices)."""
    return str(popup.selectedItem().representedObject())
