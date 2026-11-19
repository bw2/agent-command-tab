"""The panel around the list: the sort order, the search box with its "aA" checkbox, Hide Idle and expand or collapse all at the top, then the column titles; Settings at the bottom left, then a legend of the rows' two outlines, then a note while the app's hooks are missing, and, at the bottom right, Restore sessions, Save sessions and pause or resume the selected rows.

The same panel class is used in the dropdown (an NSPopover under the menu bar item), in the
sidebar and in the list window, each with its own list controller.
"""

import math

import objc
from AppKit import (NSAlignAllEdgesNearest, NSAttributedString, NSBezelStyleRecessed, NSBezierPath, NSButton, NSColor,
                    NSControlSizeSmall, NSControlStateValueOff, NSControlStateValueOn,
                    NSEdgeInsetsMake, NSEvent, NSEventModifierFlagCommand, NSFont, NSFontAttributeName,
                    NSFontWeightRegular, NSForegroundColorAttributeName,
                    NSImage, NSImageLeading, NSImageSymbolConfiguration, NSInsetRect,
                    NSLineBreakByTruncatingTail, NSMakeRect, NSPopover, NSPopoverBehaviorTransient, NSPopUpButton, NSRectFill, NSScreen,
                    NSScrollView, NSSearchField, NSTextField, NSView, NSViewController, NSViewHeightSizable, NSViewMaxXMargin,
                    NSViewMaxYMargin, NSViewMinXMargin, NSViewMinYMargin, NSViewWidthSizable, NSZeroRect)

from agent_command_tab.hooks_installer import HOOKS_ARE_MISSING, HOOKS_CANT_RUN, HOOKS_SEND_NO_EVENTS
from agent_command_tab.session_status_computation import split_agent_sessions_into_pausable_and_resumable
from agent_command_tab.user_interface.agent_session_outline_view import (FRONT_TAB_OUTLINE_COLOR_MAKER,
                                                                         NO_SESSIONS_ARE_SAVED_TOOL_TIP,
                                                                         SELECTION_OUTLINE_COLOR_MAKER, SELECTION_OUTLINE_WIDTH,
                                                                         explain_which_agent_sessions_cant_be_saved,
                                                                         list_agent_sessions_that_can_be_saved)
from agent_command_tab.user_interface.control_helpers import (add_popup_choices, is_a_command_press_of_letter,
                                                              read_popup_choice, select_popup_choice, show_symbol_on_button)
from agent_command_tab.user_interface.named_colors import appearance_is_dark
from agent_command_tab.user_interface.pointing_hand_button import PointingHandButton

# The Sort pop-up's titles for settings_store.LIST_SORT_ORDERS: each the title of a column whose
# title chooses that order when clicked (Age of the two that sort by when
# the agent started). Tab Order, the terminal's own order, has no column.
LIST_SORT_ORDER_TITLES = {"manual": "Tab Order", "activity": "Status", "alphabet": "Name", "context_percent": "Tokens",
                          "created_time": "Age", "time_since_last_message": "Last Msg", "memory_usage": "Memory", "cpu_usage": "CPU"}
POPOVER_WIDTH = 460  # before the first fit
SMALLEST_POPOVER_WIDTH = 320
LARGEST_SHARE_OF_SCREEN_WIDTH = 0.9
SPACE_FOR_SCROLLER_AND_MARGINS = 24
# Its controls sit 3 points above its bottom, so there is about as much room above them as below
# them, down to the column titles.
TOP_BAR_HEIGHT = 29
BOTTOM_BAR_HEIGHT = 28
SMALLEST_LIST_HEIGHT = 60
LIST_BOTTOM_PADDING = 6
SPACE_FOR_POPOVER_ARROW_AND_MARGIN = 40
INITIAL_PANEL_HEIGHT = 300
TOP_BAR_DRAG_DISTANCE_TO_DETACH = 6  # points: a shorter drag is a click
PANEL_SIDE_MARGIN = 6  # between the panel's edges and the expand or collapse, the Settings and the pause or resume buttons
EXPAND_OR_COLLAPSE_ALL_BUTTON_SIZE = (24, 22)
# The top bar's one expand or collapse button: (SF Symbol, text
# fallback, tooltip), by whether the list is fully expanded. The symbol, the fallback text and
# the tooltip all say what a click does. Command-E and Command-Shift-E press it in the list that
# has the keyboard, and the tooltips say so.
EXPAND_OR_COLLAPSE_ALL_KEY = "e"
EXPAND_OR_COLLAPSE_ALL_LOOKS = {False: ("rectangle.expand.vertical", "Expand all", "Expand all (⌘-e)"),
                                True: ("rectangle.compress.vertical", "Collapse all", "Collapse all (⌘-e)")}
# The bottom bar's pause or resume button, at the right: (SF Symbol, text fallback, tooltip), by what a click
# does: pause the agent
# sessions of the selected rows that run, or, once all of those are paused, resume them.
# The tooltips say how (agent_session_pausing).
PAUSE_OR_RESUME_SELECTED_ROWS_LOOKS = {
    "pause": ("pause.circle", "Pause", "Pause the selected rows' agent processes, their shells and any subprocesses that they started (a STOP signal)"),
    "resume": ("play.circle", "Resume", "Resume the selected rows' paused agent processes (a CONT signal); "
               "those suspended with Ctrl+Z are resumed by typing fg in their tabs instead")}
# The bottom bar's Restore and Save buttons, left of the pause or resume button in that order:
# (SF Symbol, text fallback, what a screen reader calls it). Restore opens the Saved sessions
# window, as "Restore sessions…" in the menus does; Save saves the sessions
# choose_what_the_save_button_saves chooses. Each keeps its place and is grayed out, its tooltip
# saying why, while it has nothing to act on.
RESTORE_SESSIONS_BUTTON_LOOK = ("tray.and.arrow.up", "Restore", "Restore sessions")
RESTORE_SESSIONS_BUTTON_TOOL_TIP = "Restore saved sessions"  # while something is saved; else NO_SESSIONS_ARE_SAVED_TOOL_TIP
SAVE_SESSIONS_BUTTON_LOOK = ("tray.and.arrow.down", "Save", "Save sessions")
# The top bar's Hide Idle / Show Idle button, left of the expand or collapse button: (title, tooltip), by
# whether idle tabs are hidden; the title says what a click does.
# Command-I presses it in the list that has the keyboard, and the tooltips say so.
IDLE_TABS_BUTTON_KEY = "i"
IDLE_TABS_BUTTON_LOOKS = {False: ("Hide Idle", "Hide tabs whose agent is idle (⌘-i)"),
                          True: ("Show Idle", "Show tabs whose agent is idle (⌘-i)")}
# Command-S and Command-Shift-S open the top bar's Sort pop-up in the list that has the keyboard,
# and its tooltip says so. While the pop-up is hidden (a list too narrow for it), they open its
# menu at its place (ListPanelView.performKeyEquivalent_).
SORT_ORDER_POPUP_KEY = "s"
SORT_ORDER_POPUP_TOOL_TIP = "Sort the list (⌘-s)"
GAP_BETWEEN_TOP_BAR_BUTTONS = 4
SMALLEST_SORT_POPUP_WIDTH = 60  # a few letters of its title and its arrows
# Before the search box (or the message line where no search box shows), and in the bottom bar
# before the legend: one gap for both, so each bar's second control starts as far from its first.
GAP_AFTER_THE_SORT_CONTROL_AND_THE_SETTINGS_BUTTON = 16
# The search box after the Sort pop-up, with the "aA" checkbox (whether case counts) after it: as
# wide as LARGEST_SEARCH_FIELD_WIDTH where there is room, narrower in a narrow sidebar, the
# checkbox hidden where even the narrowest box wouldn't leave it room, and the box hidden below
# SMALLEST_SEARCH_FIELD_WIDTH (fit_top_bar_to_width).
LARGEST_SEARCH_FIELD_WIDTH = 220
SMALLEST_SEARCH_FIELD_WIDTH = 80  # the magnifying glass, a few letters, and the button that empties it
SEARCH_FIELD_PLACEHOLDER = "Search"
SEARCH_FIELD_TOOL_TIP = ("Show only the windows whose title holds this text, with all their tabs, the tabs whose title "
                         "holds it, and, dimmed, those whose conversation does (prompts, replies and the commands the "
                         "agent ran) (⌘-f or /)")
CASE_SENSITIVE_CHECKBOX_TITLE = "aA"
CASE_SENSITIVE_CHECKBOX_TOOL_TIP = "Toggle whether the search is case sensitive"
# A message line narrower than this beside the sort control and Hide Idle shows too little to
# read, so they step aside for it (fit_top_bar_to_width).
SMALLEST_MESSAGE_WIDTH_BESIDE_THE_CONTROLS = 120
HOOKS_NOTE_TOOL_TIP = ("These hooks provide AgentCommandTab more details about agent status, including whether it "
                       "launched background processes, etc.")
# The notes about hooks that are installed but don't work, which Install Hooks wouldn't fix.
HOOKS_CANT_RUN_TOOL_TIP = ("The Python this copy of AgentCommandTab runs on is gone (for example, Homebrew removed the "
                           "Python version its pipx environment was made with), or the script its hooks run is, so "
                           "its hooks can't run. Reinstall it (with pipx: pipx reinstall agent-command-tab), then "
                           "quit it and start it again.")
HOOKS_SEND_NO_EVENTS_TOOL_TIP = ("Claude Code sessions started after the hooks were installed haven't sent "
                                 "AgentCommandTab any events, so background tasks and turn ends may show wrong. "
                                 "Check that hooks aren't turned off in Claude Code: type /hooks in a session.")
GAP_BEFORE_THE_HOOKS_NOTE = 8  # after the legend, or after the Settings button where the legend doesn't show
# The bottom bar's legend of the rows' two outlines, after the Settings button: its title, then, for
# each outline, a small square stroked like it (the maker of its color) and what it marks. The
# current tab is the one tab in front, whose row has the outline in the accent color (many tabs can
# be on screen at once).
ROW_OUTLINES_LEGEND_TITLE = "Legend:"
ROW_OUTLINES_LEGEND_ENTRIES = ((FRONT_TAB_OUTLINE_COLOR_MAKER, "= current tab"), (SELECTION_OUTLINE_COLOR_MAKER, "= selected"))
ROW_OUTLINES_LEGEND_SQUARE_SIZE = 9  # a little more than the height of the small text's capital letters
ROW_OUTLINES_LEGEND_SQUARE_CORNER_RADIUS = 2
GAP_BEFORE_A_LEGEND_SQUARE = 8
GAP_AFTER_A_LEGEND_SQUARE = 4


def choose_what_the_save_button_saves(agent_session_entries_of_the_selected_rows, a_row_is_selected, every_agent_session_entry):
    """Returns (the agent sessions the bottom bar's Save button saves, its tooltip): those of the
    selected rows, or, while no row is selected, every one in the list (also those whose rows
    Hide Idle or a search hides), that can be saved (list_agent_sessions_that_can_be_saved). With
    none to save the button is grayed out, and the tooltip says why."""
    agent_session_entries = agent_session_entries_of_the_selected_rows if a_row_is_selected else every_agent_session_entry
    if not agent_session_entries:
        return [], "There are no sessions to save"
    sessions_to_save = list_agent_sessions_that_can_be_saved(agent_session_entries)
    what_isnt_saved = explain_which_agent_sessions_cant_be_saved(agent_session_entries, sessions_to_save)
    if not sessions_to_save:
        return [], what_isnt_saved
    which_sessions = "the sessions of selected rows" if a_row_is_selected else "every session in the list (no row is selected)"
    what_it_saves = f"Save {which_sessions} so that you can restart them later even after they exit"
    return sessions_to_save, f"{what_it_saves}. {what_isnt_saved}" if what_isnt_saved else what_it_saves


def choose_hooks_note(hooks_status_per_agent_tool, listed_agent_tool_names):
    """Returns (the bottom bar's note about the app's hooks, or "" for none, its tooltip, the
    agent tools its Install Hooks button installs them for). Only tools with a session in the list count, so
    someone who never runs Claude Code isn't
    asked to install its hooks. Hooks that can't run come first: nothing else works until the app
    is reinstalled.

    Args:
        hooks_status_per_agent_tool: ComputerSnapshot.hooks_status_per_agent_tool.
        listed_agent_tool_names: The agent tools of the list's sessions.
    """
    def list_listed_agent_tools_whose_hooks_are(wanted_status):
        return [agent_tool_name for agent_tool_name, status in hooks_status_per_agent_tool
                if status == wanted_status and agent_tool_name in listed_agent_tool_names]
    agent_tool_names_whose_hooks_cant_run = list_listed_agent_tools_whose_hooks_are(HOOKS_CANT_RUN)
    if agent_tool_names_whose_hooks_cant_run:
        return (f"{' and '.join(agent_tool_names_whose_hooks_cant_run)} hooks can't run: reinstall AgentCommandTab",
                HOOKS_CANT_RUN_TOOL_TIP, [])
    missing_agent_tool_names = list_listed_agent_tools_whose_hooks_are(HOOKS_ARE_MISSING)
    if missing_agent_tool_names:
        return f"{' and '.join(missing_agent_tool_names)} hooks not installed yet", HOOKS_NOTE_TOOL_TIP, missing_agent_tool_names
    if list_listed_agent_tools_whose_hooks_are(HOOKS_SEND_NO_EVENTS):
        return "No events from the Claude Code hooks", HOOKS_SEND_NO_EVENTS_TOOL_TIP, []
    return "", HOOKS_NOTE_TOOL_TIP, []


def make_gray_pill_button(title, target, action_name):
    """Returns a small button drawn as a gray pill, like the top bar's Sort pop-up (Hide Idle,
    Settings, Install Hooks), with the pointing hand."""
    button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(0, 0, 80, 20))
    button.setTitle_(title)
    button.setBezelStyle_(NSBezelStyleRecessed)
    button.setShowsBorderOnlyWhileMouseInside_(False)
    button.setControlSize_(NSControlSizeSmall)
    button.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
    button.setTarget_(target)
    button.setAction_(action_name)
    return button


def measure_text_baseline_y_in_its_frame(control):
    """Returns how far above the bottom of the control's frame the baseline of its text is (AppKit
    measures it from the frame less the insets it lines controls up by)."""
    return control.alignmentRectInsets().bottom + control.lastBaselineOffsetFromBottom()


def click_control_on_a_command_press_of_letter(control, letter, event):
    """Clicks the control, when it shows, for Command and the letter, with or without Shift (a
    control's own key equivalent matches only one of them). Returns whether it did."""
    if control.isHiddenOrHasHiddenAncestor() or not is_a_command_press_of_letter(
            letter, event.charactersIgnoringModifiers(), event.modifierFlags()):
        return False
    control.performClick_(None)
    return True


class ExpandOrCollapseAllButton(PointingHandButton):
    """The top bar's expand or collapse button, which Command-E and Command-Shift-E press too
    while it shows."""

    def performKeyEquivalent_(self, event):
        return (click_control_on_a_command_press_of_letter(self, EXPAND_OR_COLLAPSE_ALL_KEY, event)
                or objc.super(ExpandOrCollapseAllButton, self).performKeyEquivalent_(event))


class SortOrderPopUpButton(NSPopUpButton):
    """The top bar's Sort pop-up, whose menu Command-S and Command-Shift-S open while it shows
    (AppKit doesn't ask a hidden view for its keys: the panel opens the menu of a hidden pop-up,
    ListPanelView.performKeyEquivalent_). The
    open menu takes the keys (the arrows, Return and typing choose an order, Esc closes it), and
    the list's rows have the keyboard again once it closes, as they never lost it."""

    def performKeyEquivalent_(self, event):
        return (click_control_on_a_command_press_of_letter(self, SORT_ORDER_POPUP_KEY, event)
                or objc.super(SortOrderPopUpButton, self).performKeyEquivalent_(event))


class ListSearchField(NSSearchField):
    """The top bar's search box. It tells its panel when it takes the keyboard (a click, or
    Command-F or "/" in the list), so a sidebar keeps the keyboard while you type in it."""

    def canBecomeKeyView(self):
        # Never the view a window gives the keyboard to by itself (as it opens, or with Tab): the
        # list keeps it, and the box takes it only from a click, Command-F or "/".
        return False

    def becomeFirstResponder(self):
        became_first_responder = objc.super(ListSearchField, self).becomeFirstResponder()
        if became_first_responder:
            self.panel_controller.on_search_box_took_the_keyboard()
        return became_first_responder


class RowOutlinesLegendView(NSView):
    """The bottom bar's legend of the rows' two outlines: ROW_OUTLINES_LEGEND_TITLE, then, for each
    of ROW_OUTLINES_LEGEND_ENTRIES, a small square stroked as the rows stroke that outline (the same
    color maker and line width) and the text of what it marks, in the hooks note's font and color.
    It draws them each time AppKit draws it, as the rows draw their outlines, so its squares follow
    light and dark mode and the accent color as those do."""

    def initWithHeight_textBaselineY_(self, height, text_baseline_y):
        """Makes the legend as wide as its texts and squares need.

        Args:
            height: The height of the view, the bottom bar's.
            text_baseline_y: Where the texts' baseline is, from the view's bottom.
        """
        self = objc.super(RowOutlinesLegendView, self).initWithFrame_(NSZeroRect)
        if self is None:
            return None
        font = NSFont.systemFontOfSize_(NSFont.smallSystemFontSize())
        self.text_baseline_y = text_baseline_y
        # The squares' middle is the middle of the texts' capital letters.
        self.square_y = text_baseline_y + (font.capHeight() - ROW_OUTLINES_LEGEND_SQUARE_SIZE) / 2
        text_attributes = {NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.secondaryLabelColor()}
        title = NSAttributedString.alloc().initWithString_attributes_(ROW_OUTLINES_LEGEND_TITLE, text_attributes)
        self.texts_and_their_x = [(title, 0)]
        self.square_color_makers_and_their_x = []
        x = math.ceil(title.size().width)  # whole points, so each square starts on a whole one
        for outline_color_maker, text in ROW_OUTLINES_LEGEND_ENTRIES:
            x += GAP_BEFORE_A_LEGEND_SQUARE
            self.square_color_makers_and_their_x.append((outline_color_maker, x))
            x += ROW_OUTLINES_LEGEND_SQUARE_SIZE + GAP_AFTER_A_LEGEND_SQUARE
            attributed_text = NSAttributedString.alloc().initWithString_attributes_(text, text_attributes)
            self.texts_and_their_x.append((attributed_text, x))
            x += math.ceil(attributed_text.size().width)
        self.setFrameSize_((x, height))
        return self

    def drawRect_(self, rect):
        for attributed_text, x in self.texts_and_their_x:
            # With no drawing option the rectangle's origin is the text's baseline, and its height isn't used.
            attributed_text.drawWithRect_options_(NSMakeRect(x, self.text_baseline_y, self.bounds().size.width - x, 0), 0)
        for outline_color_maker, x in self.square_color_makers_and_their_x:
            # The square's outer edges on whole pixels, so its lines are as sharp as a row's outline.
            square_rect = self.backingAlignedRect_options_(
                NSMakeRect(x, self.square_y, ROW_OUTLINES_LEGEND_SQUARE_SIZE, ROW_OUTLINES_LEGEND_SQUARE_SIZE), NSAlignAllEdgesNearest)
            outline = NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(
                NSInsetRect(square_rect, SELECTION_OUTLINE_WIDTH / 2, SELECTION_OUTLINE_WIDTH / 2),
                ROW_OUTLINES_LEGEND_SQUARE_CORNER_RADIUS, ROW_OUTLINES_LEGEND_SQUARE_CORNER_RADIUS)
            outline.setLineWidth_(SELECTION_OUTLINE_WIDTH)
            outline_color_maker().setStroke()
            outline.stroke()


class ListPanelView(NSView):
    """The panel's root view: tells its controller when its size changes, so the top bar fits,
    and when the top bar's empty space is dragged (the dropdown or the sidebar then turns into
    the list window). The top bar's controls take
    their own clicks. It answers the Sort pop-up's keys while the pop-up is hidden."""

    def acceptsFirstMouse_(self, event):
        return True  # the sidebar's top bar drags from the first click, though the dashboard isn't the active app

    def performKeyEquivalent_(self, event):
        # AppKit asks only the views that show for their keys, so a hidden Sort pop-up never gets
        # Command-S. A list too narrow for the pop-up may have dropped the column titles that
        # sort it too, so the panel opens the pop-up's menu for it.
        controller = getattr(self, "controller", None)
        if (controller is not None and controller.list_sort_order_popup.isHidden()
                and is_a_command_press_of_letter(SORT_ORDER_POPUP_KEY, event.charactersIgnoringModifiers(), event.modifierFlags())):
            controller.open_the_menu_of_the_hidden_sort_popup()
            return True
        return objc.super(ListPanelView, self).performKeyEquivalent_(event)

    def drawRect_(self, rect):
        # In dark mode, the top bar, the column titles and the bottom bar are filled with the list's
        # own background color (the outline view's default, controlBackgroundColor) rather than
        # left on the lighter gray of the window, the popover or the sidebar around it. The list
        # itself, which AppKit doesn't fill solid in that color, shows lighter than the bars. Light mode keeps
        # that gray around the list, whose
        # rows are on a very light gray there (named_colors.choose_list_background_color). AppKit redraws
        # views when the mode changes.
        if appearance_is_dark(self.effectiveAppearance()):
            NSColor.controlBackgroundColor().set()
            NSRectFill(rect)

    def resizeSubviewsWithOldSize_(self, old_size):
        objc.super(ListPanelView, self).resizeSubviewsWithOldSize_(old_size)
        controller = getattr(self, "controller", None)
        if controller is not None:
            controller.fit_top_bar_to_width()
            controller.fit_bottom_bar_to_width()

    def mouseDown_(self, event):
        point = self.convertPoint_fromView_(event.locationInWindow(), None)
        is_on_the_top_bar = point.y >= self.bounds().size.height - TOP_BAR_HEIGHT
        self.top_bar_drag_start = NSEvent.mouseLocation() if is_on_the_top_bar else None  # screen coordinates
        if not is_on_the_top_bar:
            objc.super(ListPanelView, self).mouseDown_(event)

    def mouseDragged_(self, event):
        start = getattr(self, "top_bar_drag_start", None)
        pointer = NSEvent.mouseLocation()
        if start is not None and math.hypot(pointer.x - start.x, pointer.y - start.y) >= TOP_BAR_DRAG_DISTANCE_TO_DETACH:
            self.top_bar_drag_start = None
            self.controller.list_controller.owner.top_bar_dragged(self.controller, (start.x, start.y))

    def mouseUp_(self, event):
        # The press on the top bar is over. Its start kept, a later press elsewhere that passes its
        # drags up to this view (on a column title) would count as a drag of the top bar from there.
        self.top_bar_drag_start = None
        objc.super(ListPanelView, self).mouseUp_(event)


class ListPanelController(NSViewController):
    """Holds a list controller's outline view in a scroll view, with a top and a bottom bar and the
    list controller's row of column titles between the top bar and the list.

    The owner is the list controller's owner (see AgentSessionListController); the panel also
    calls its open_settings(), list_sort_order_chosen(list_sort_order, list_sort_order_is_reversed)
    (as the list controller does when a column's title is clicked),
    idle_tabs_are_hidden_chosen(idle_tabs_are_hidden),
    search_is_case_sensitive_chosen(search_is_case_sensitive) (the "aA" checkbox),
    top_bar_dragged(list_panel_controller, start_point_on_screen) and
    install_hooks(agent_tool_names) (the bottom bar's Install Hooks button).
    """

    def initWithListController_(self, list_controller):
        self = objc.super(ListPanelController, self).initWithNibName_bundle_(None, None)
        if self is None:
            return None
        self.list_controller = list_controller
        # Called when the search box takes the keyboard; the sidebar sets it, to keep the keyboard
        # while you type there without making the dashboard the active app.
        self.on_search_box_took_the_keyboard = lambda: None
        # Gives the list's rows the keyboard of a search box that hides while it has it
        # (fit_top_bar_to_width); the sidebar sets its own way, since in its panel the list takes
        # the keyboard only through SidebarController.give_the_list_the_keyboard_focus.
        self.give_the_list_the_keyboard = lambda: self.view().window().makeFirstResponder_(self.list_controller.outline_view)
        return self

    def loadView(self):
        width, height = POPOVER_WIDTH, INITIAL_PANEL_HEIGHT
        root_view = ListPanelView.alloc().initWithFrame_(NSMakeRect(0, 0, width, height))
        scroll_view = NSScrollView.alloc().initWithFrame_(
            NSMakeRect(0, BOTTOM_BAR_HEIGHT, width, height - TOP_BAR_HEIGHT - BOTTOM_BAR_HEIGHT))
        scroll_view.setDocumentView_(self.list_controller.outline_view)
        self.list_controller.outline_view.sizeLastColumnToFit()  # a new column is only 100 points wide
        scroll_view.setHasVerticalScroller_(True)
        scroll_view.setAutohidesScrollers_(True)
        scroll_view.setAutomaticallyAdjustsContentInsets_(False)  # no blank band above the first row
        scroll_view.setContentInsets_(NSEdgeInsetsMake(0, 0, 0, 0))
        scroll_view.setDrawsBackground_(False)
        scroll_view.setAutoresizingMask_(NSViewWidthSizable | NSViewHeightSizable)
        root_view.addSubview_(scroll_view)
        # The column titles, between the top bar and the list, as tall
        # as their titles need (fit_list_under_column_titles).
        column_titles_row_view = self.list_controller.column_titles_row_view
        column_titles_row_view.setFrame_(NSMakeRect(0, height - TOP_BAR_HEIGHT, width, 0))
        column_titles_row_view.setAutoresizingMask_(NSViewWidthSizable | NSViewMinYMargin)
        root_view.addSubview_(column_titles_row_view)
        self.list_controller.on_column_titles_row_height_changed = self.fit_list_under_column_titles

        # Top bar: the sort order on the left; at the right, Hide Idle and one button that expands
        # or collapses all; a message line after the sort order.
        self.sort_label = NSTextField.labelWithString_("Sort:")
        self.sort_label.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.sort_label.sizeToFit()
        self.sort_label.setFrameOrigin_((8, height - TOP_BAR_HEIGHT + 6))
        self.sort_label.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.sort_label)
        self.list_sort_order_popup = SortOrderPopUpButton.alloc().initWithFrame_pullsDown_(NSMakeRect(0, 0, 90, 20), False)
        self.list_sort_order_popup.setToolTip_(SORT_ORDER_POPUP_TOOL_TIP)
        # A gray pill like the buttons beside it, and narrower than the standard style (the inline
        # style draws no text in a pop-up).
        self.list_sort_order_popup.setBezelStyle_(NSBezelStyleRecessed)
        self.list_sort_order_popup.setControlSize_(NSControlSizeSmall)
        self.list_sort_order_popup.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        add_popup_choices(self.list_sort_order_popup, LIST_SORT_ORDER_TITLES)
        self.list_sort_order_popup.sizeToFit()
        self.sort_popup_width_for_its_widest_title = self.list_sort_order_popup.frame().size.width
        self.list_sort_order_popup.setTarget_(self)
        self.list_sort_order_popup.setAction_("listSortOrderChosen:")
        self.list_sort_order_popup.setFrameOrigin_((0, height - TOP_BAR_HEIGHT + 3))  # fit_top_bar_to_width sets x
        self.list_sort_order_popup.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.list_sort_order_popup)
        self.idle_tabs_button = make_gray_pill_button("", self, "idleTabsButtonClicked:")
        # As wide as the wider of its two titles, so the top bar doesn't shift when it toggles.
        idle_tabs_button_widths = []
        for title, _ in IDLE_TABS_BUTTON_LOOKS.values():
            self.idle_tabs_button.setTitle_(title)
            self.idle_tabs_button.sizeToFit()
            idle_tabs_button_widths.append(self.idle_tabs_button.frame().size.width)
        self.idle_tabs_button.setFrame_(NSMakeRect(  # fit_top_bar_to_width sets x
            0, height - TOP_BAR_HEIGHT + 3, max(idle_tabs_button_widths), self.list_sort_order_popup.frame().size.height))
        self.idle_tabs_button.setKeyEquivalent_(IDLE_TABS_BUTTON_KEY)
        self.idle_tabs_button.setKeyEquivalentModifierMask_(NSEventModifierFlagCommand)
        self.idle_tabs_button.setAutoresizingMask_(NSViewMinYMargin | NSViewMinXMargin)
        root_view.addSubview_(self.idle_tabs_button)
        self.update_idle_tabs_button()
        button_width, button_height = EXPAND_OR_COLLAPSE_ALL_BUTTON_SIZE
        self.expand_or_collapse_all_button = ExpandOrCollapseAllButton.alloc().initWithFrame_(NSMakeRect(  # update_top_and_bottom_bar_buttons gives its look
            width - PANEL_SIDE_MARGIN - button_width, height - TOP_BAR_HEIGHT + 3, button_width, button_height))
        self.expand_or_collapse_all_button.setBordered_(False)
        self.expand_or_collapse_all_button.setTarget_(self)
        self.expand_or_collapse_all_button.setAction_("expandOrCollapseAllClicked:")
        self.expand_or_collapse_all_button.setAutoresizingMask_(NSViewMinYMargin | NSViewMinXMargin)  # stays at the top right
        root_view.addSubview_(self.expand_or_collapse_all_button)
        self.list_controller.on_snapshot_expansion_or_selection_changed = self.update_top_and_bottom_bar_buttons
        self.message_text_field = NSTextField.labelWithString_("")
        self.message_text_field.setFrame_(NSMakeRect(0, height - TOP_BAR_HEIGHT + 4, 0, 18))  # fit_top_bar_to_width sets x and width
        self.message_text_field.setTextColor_(NSColor.secondaryLabelColor())
        self.message_text_field.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.message_text_field)
        self.sort_control_and_hide_idle_step_aside_for_the_message = True  # show_message's
        # After the sort order: the search box and its "aA" checkbox (fit_top_bar_to_width places them).
        self.search_field = ListSearchField.alloc().initWithFrame_(NSMakeRect(0, height - TOP_BAR_HEIGHT + 4, LARGEST_SEARCH_FIELD_WIDTH, 20))
        self.search_field.panel_controller = self
        self.search_field.setControlSize_(NSControlSizeSmall)
        self.search_field.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.search_field.setPlaceholderString_(SEARCH_FIELD_PLACEHOLDER)
        self.search_field.setToolTip_(SEARCH_FIELD_TOOL_TIP)
        self.search_field.setFrameSize_((LARGEST_SEARCH_FIELD_WIDTH, self.search_field.fittingSize().height))
        self.search_field.setDelegate_(self)  # controlTextDidChange_ and the keys (control_textView_doCommandBySelector_)
        self.search_field.setTarget_(self)  # its button that empties it
        self.search_field.setAction_("searchFieldChanged:")
        self.search_field.setAutoresizingMask_(NSViewMinYMargin)
        root_view.addSubview_(self.search_field)
        self.case_sensitive_checkbox = NSButton.checkboxWithTitle_target_action_(
            CASE_SENSITIVE_CHECKBOX_TITLE, self, "caseSensitiveCheckboxClicked:")
        self.case_sensitive_checkbox.setControlSize_(NSControlSizeSmall)
        self.case_sensitive_checkbox.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.case_sensitive_checkbox.setToolTip_(CASE_SENSITIVE_CHECKBOX_TOOL_TIP)
        self.case_sensitive_checkbox.sizeToFit()
        self.case_sensitive_checkbox.setAutoresizingMask_(NSViewMinYMargin)
        sort_popup_middle_y = self.list_sort_order_popup.frame().origin.y + self.list_sort_order_popup.frame().size.height / 2
        for control in (self.search_field, self.case_sensitive_checkbox):  # level with the Sort pop-up
            control.setFrameOrigin_((0, round(sort_popup_middle_y - control.frame().size.height / 2)))
        root_view.addSubview_(self.case_sensitive_checkbox)
        self.list_controller.on_search_keys_pressed = self.put_the_keyboard_in_the_search_box
        self.list_controller.on_search_text_cleared = self.empty_the_search_box

        # Bottom bar: a Settings button with its name, easier to see than an icon, at the left, a
        # gray pill like the top bar's controls, and, at the right, one button that pauses or
        # resumes the selected rows, with the Save button left of it and the Restore button left
        # of that; a new window with an agent session is the "+" row at the end of the list.
        self.settings_button = make_gray_pill_button("Settings", self, "settingsClicked:")
        # A gear before its name, as tall as the small text; the name alone where the symbol is missing.
        gear_image = NSImage.imageWithSystemSymbolName_accessibilityDescription_("gearshape", None)
        if gear_image is not None:
            self.settings_button.setImage_(gear_image.imageWithSymbolConfiguration_(
                NSImageSymbolConfiguration.configurationWithPointSize_weight_(NSFont.smallSystemFontSize(), NSFontWeightRegular)))
            self.settings_button.setImagePosition_(NSImageLeading)
        self.settings_button.sizeToFit()
        self.settings_button.setFrameOrigin_((PANEL_SIDE_MARGIN, (BOTTOM_BAR_HEIGHT - self.settings_button.frame().size.height) / 2))
        self.settings_button.setAutoresizingMask_(NSViewMaxYMargin | NSViewMaxXMargin)  # stays at the bottom left
        root_view.addSubview_(self.settings_button)
        # The buttons at the right, the rightmost first; update_top_and_bottom_bar_buttons gives the
        # pause or resume button its look and each its tooltip.
        bottom_right_buttons = []
        for action_name in ("pauseOrResumeSelectedRowsClicked:", "saveSessionsClicked:", "restoreSessionsClicked:"):
            button = PointingHandButton.alloc().initWithFrame_(NSMakeRect(
                width - PANEL_SIDE_MARGIN - button_width - len(bottom_right_buttons) * (button_width + GAP_BETWEEN_TOP_BAR_BUTTONS),
                (BOTTOM_BAR_HEIGHT - button_height) / 2, button_width, button_height))
            button.setBordered_(False)
            button.setTarget_(self)
            button.setAction_(action_name)
            button.setAutoresizingMask_(NSViewMaxYMargin | NSViewMinXMargin)  # stays at the bottom right
            root_view.addSubview_(button)
            bottom_right_buttons.append(button)
        self.pause_or_resume_selected_rows_button, self.save_sessions_button, self.restore_sessions_button = bottom_right_buttons
        show_symbol_on_button(self.save_sessions_button, *SAVE_SESSIONS_BUTTON_LOOK)
        show_symbol_on_button(self.restore_sessions_button, *RESTORE_SESSIONS_BUTTON_LOOK)
        # After the Settings button, the legend of the rows' outlines; fit_bottom_bar_to_width shows
        # it where it fits. Its texts, and the hooks note's after it, sit on the baseline of the
        # button's title.
        settings_button_frame = self.settings_button.frame()
        self.bottom_bar_text_baseline_y = settings_button_frame.origin.y + measure_text_baseline_y_in_its_frame(self.settings_button)
        self.row_outlines_legend_view = RowOutlinesLegendView.alloc().initWithHeight_textBaselineY_(
            BOTTOM_BAR_HEIGHT, self.bottom_bar_text_baseline_y)
        self.row_outlines_legend_view.setFrameOrigin_((
            settings_button_frame.origin.x + settings_button_frame.size.width + GAP_AFTER_THE_SORT_CONTROL_AND_THE_SETTINGS_BUTTON, 0))
        root_view.addSubview_(self.row_outlines_legend_view)
        # After the legend, while the app's hooks are missing for an agent tool with a session
        # in the list, a note and a button that installs them.
        self.hooks_note_text_field = NSTextField.labelWithString_("")
        self.hooks_note_text_field.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
        self.hooks_note_text_field.setTextColor_(NSColor.secondaryLabelColor())
        self.hooks_note_text_field.cell().setLineBreakMode_(NSLineBreakByTruncatingTail)
        self.hooks_note_text_field.setToolTip_(HOOKS_NOTE_TOOL_TIP)  # update_top_and_bottom_bar_buttons sets the note's own
        root_view.addSubview_(self.hooks_note_text_field)
        self.install_hooks_button = make_gray_pill_button("Install Hooks", self, "installHooksClicked:")
        self.install_hooks_button.sizeToFit()
        self.install_hooks_button.setToolTip_(HOOKS_NOTE_TOOL_TIP)
        root_view.addSubview_(self.install_hooks_button)
        self.agent_tool_names_to_install_hooks_for = []
        self.shown_bars_state = None  # what update_top_and_bottom_bar_buttons last showed
        self.setView_(root_view)
        root_view.controller = self
        self.update_top_and_bottom_bar_buttons()
        self.fit_top_bar_to_width()
        self.fit_list_under_column_titles()

    @objc.python_method
    def fit_list_under_column_titles(self):
        """Gives the row of column titles the height its titles need (none, hidden, while no
        titled column shows) and the list the room below it."""
        bounds = self.view().bounds()
        column_titles_row_height = self.list_controller.measure_column_titles_row_height()
        list_top = bounds.size.height - TOP_BAR_HEIGHT - column_titles_row_height
        column_titles_row_view = self.list_controller.column_titles_row_view
        column_titles_row_view.setFrame_(NSMakeRect(0, list_top, bounds.size.width, column_titles_row_height))
        column_titles_row_view.setHidden_(not column_titles_row_height)  # its titles would show above a 0-high row
        self.list_controller.outline_view.enclosingScrollView().setFrame_(
            NSMakeRect(0, BOTTOM_BAR_HEIGHT, bounds.size.width, list_top - BOTTOM_BAR_HEIGHT))

    @objc.python_method
    def fit_top_bar_to_width(self):
        """Puts the expand or collapse all button at the right end and the Hide Idle button just
        left of it. The sort control and then the search box with its "aA" checkbox
        fit between the panel's left edge and those buttons. The search box keeps at least
        SMALLEST_SEARCH_FIELD_WIDTH, with its checkbox where there is room for both: the sort
        control shows with its "Sort:" label when there is
        room, without it in a narrower one, and then with the pop-up narrowed, cutting its title
        short, until it hides below SMALLEST_SORT_POPUP_WIDTH (only in a sidebar or a list window,
        where the column titles that still show sort the list and Command-S opens the pop-up's
        menu at its place). The search box takes the room left, up to
        LARGEST_SEARCH_FIELD_WIDTH. Where each step happens depends on how wide
        macOS draws the controls. The message line takes the room left before the buttons, so it never covers them,
        while the search box keeps its smallest width;
        while it shows a message that asks for it (the app restarts, so there are no rows to sort
        or hide; show_message), or one that would get less than SMALLEST_MESSAGE_WIDTH_BESIDE_THE_CONTROLS
        beside them (a narrow sidebar), the sort control, the search box and the Hide Idle button step aside for it,
        the search box only while it is empty. The rows take the keyboard of a search box that hides
        while it has it, so the list's keys go on working."""
        has_a_message = bool(self.message_text_field.stringValue())
        search_box_had_the_keyboard = self.search_field.currentEditor() is not None
        message_asks_the_controls_to_step_aside = has_a_message and self.sort_control_and_hide_idle_step_aside_for_the_message
        message_width = self.lay_out_top_bar(message_asks_the_controls_to_step_aside)
        if (has_a_message and not message_asks_the_controls_to_step_aside
                and message_width < SMALLEST_MESSAGE_WIDTH_BESIDE_THE_CONTROLS):
            self.lay_out_top_bar(True)  # only after a layout that kept the controls: one where they stepped aside can't give more
        if search_box_had_the_keyboard and self.search_field.isHidden():
            self.give_the_list_the_keyboard()

    @objc.python_method
    def lay_out_top_bar(self, controls_step_aside_for_a_message):
        """Lays out the top bar as fit_top_bar_to_width describes, with the sort control, the
        search box and the Hide Idle button stepping aside for the message when controls_step_aside_for_a_message;
        returns the message line's width."""
        label_x, label_width = self.sort_label.frame().origin.x, self.sort_label.frame().size.width
        popup_frame = self.list_sort_order_popup.frame()
        self.idle_tabs_button.setHidden_(controls_step_aside_for_a_message)
        right_end_x = self.view().bounds().size.width - PANEL_SIDE_MARGIN  # then the left end of the leftmost button placed
        gap_before_the_buttons_placed = 0
        for button in (self.expand_or_collapse_all_button, self.idle_tabs_button):  # side by side from the right end
            if not button.isHidden():
                right_end_x -= gap_before_the_buttons_placed + button.frame().size.width
                button.setFrameOrigin_((right_end_x, button.frame().origin.y))
                gap_before_the_buttons_placed = GAP_BETWEEN_TOP_BAR_BUTTONS
        room_right_edge = right_end_x - gap_before_the_buttons_placed
        # The search box at its smallest, with its checkbox where both fit, keeps its room before the sort control's.
        # A search box that holds a search stays for a message too (without its checkbox), so the list is never
        # filtered with no box to show the search or clear it.
        checkbox_width = self.case_sensitive_checkbox.frame().size.width
        checkbox_room = GAP_BETWEEN_TOP_BAR_BUTTONS + checkbox_width
        search_field_shows = ((not controls_step_aside_for_a_message or bool(self.search_field.stringValue()))
                              and room_right_edge - label_x >= SMALLEST_SEARCH_FIELD_WIDTH)
        checkbox_shows = (search_field_shows and not controls_step_aside_for_a_message
                          and room_right_edge - label_x >= SMALLEST_SEARCH_FIELD_WIDTH + checkbox_room)
        smallest_search_room = (SMALLEST_SEARCH_FIELD_WIDTH + (checkbox_room if checkbox_shows else 0)
                                + GAP_AFTER_THE_SORT_CONTROL_AND_THE_SETTINGS_BUTTON if search_field_shows else 0)
        sort_room_right_edge = room_right_edge - smallest_search_room
        popup_x_after_label = label_x + label_width + 2
        label_fits = (not controls_step_aside_for_a_message
                      and popup_x_after_label + self.sort_popup_width_for_its_widest_title <= sort_room_right_edge)
        popup_x = popup_x_after_label if label_fits else label_x
        popup_width = max(min(self.sort_popup_width_for_its_widest_title, sort_room_right_edge - popup_x), 0)
        popup_fits = not controls_step_aside_for_a_message and popup_width >= SMALLEST_SORT_POPUP_WIDTH
        self.sort_label.setHidden_(not label_fits)
        self.list_sort_order_popup.setHidden_(not popup_fits)
        self.list_sort_order_popup.setFrame_(NSMakeRect(popup_x, popup_frame.origin.y, popup_width, popup_frame.size.height))
        search_x = popup_x + popup_width + GAP_AFTER_THE_SORT_CONTROL_AND_THE_SETTINGS_BUTTON if popup_fits else label_x
        has_a_message = bool(self.message_text_field.stringValue())
        search_field_width = (SMALLEST_SEARCH_FIELD_WIDTH if has_a_message  # the message takes the rest
                              else max(min(LARGEST_SEARCH_FIELD_WIDTH,
                                           room_right_edge - search_x - (checkbox_room if checkbox_shows else 0)),
                                       SMALLEST_SEARCH_FIELD_WIDTH))
        self.search_field.setHidden_(not search_field_shows)
        self.case_sensitive_checkbox.setHidden_(not checkbox_shows)
        search_frame = self.search_field.frame()
        self.search_field.setFrame_(NSMakeRect(search_x, search_frame.origin.y, search_field_width, search_frame.size.height))
        checkbox_x = search_x + search_field_width + GAP_BETWEEN_TOP_BAR_BUTTONS
        self.case_sensitive_checkbox.setFrameOrigin_((checkbox_x, self.case_sensitive_checkbox.frame().origin.y))
        message_x = (checkbox_x + checkbox_width + 8 if checkbox_shows else search_x + search_field_width + 8 if search_field_shows
                     else search_x)
        message_frame = self.message_text_field.frame()
        message_width = max(right_end_x - message_x - 8, 0)
        self.message_text_field.setFrame_(NSMakeRect(message_x, message_frame.origin.y, message_width, message_frame.size.height))
        return message_width

    def expandOrCollapseAllClicked_(self, sender):
        """Collapses all monitor and window rows when the list is fully expanded, and otherwise
        expands them all (the tab rows stay open or closed as they are)."""
        self.list_controller.set_all_expanded(not self.list_controller.is_fully_expanded())

    def pauseOrResumeSelectedRowsClicked_(self, sender):
        """Pauses the agent sessions of the selected rows that run, or, once all of those are
        paused, resumes them."""
        sessions_to_pause, sessions_to_resume = split_agent_sessions_into_pausable_and_resumable(
            self.list_controller.list_agent_session_entries_of_the_selected_rows())
        pause = bool(sessions_to_pause)
        self.list_controller.pause_or_resume_agent_sessions(
            [entry.session_id for entry in (sessions_to_pause if pause else sessions_to_resume)], pause=pause)

    @objc.python_method
    def choose_what_the_save_button_saves_now(self):
        """Returns (the agent sessions the Save button saves now, its tooltip):
        choose_what_the_save_button_saves for the list's selection and rows."""
        return choose_what_the_save_button_saves(self.list_controller.list_agent_session_entries_of_the_selected_rows(),
                                                 bool(self.list_controller.list_selected_items()),
                                                 self.list_controller.list_agent_session_entries())

    def saveSessionsClicked_(self, sender):
        """Saves the agent sessions of the selected rows, or, while no row is selected, every one
        in the list, as a row menu's Save does for its rows."""
        self.list_controller.owner.save_agent_sessions([entry.session_id for entry in self.choose_what_the_save_button_saves_now()[0]])

    def restoreSessionsClicked_(self, sender):
        self.list_controller.owner.open_saved_sessions_window()

    @objc.python_method
    def show_whether_sessions_are_saved(self):
        """Shows the Restore button as clickable or grayed out as the owner now says
        (has_saved_agent_sessions). The app controller calls it when the saved list changes, which
        no snapshot or selection change tells."""
        self.view()
        self.update_top_and_bottom_bar_buttons()

    @objc.python_method
    def update_top_and_bottom_bar_buttons(self):
        """Gives the expand or collapse button (top bar) and the pause or resume button (bottom
        bar) the look of the list's current state and shows each only when it applies; the top bar
        refits when its button appears or goes. Called after every snapshot, expansion and selection
        change, so the controls are only touched when what they show changes.

        The expand or collapse button shows while the list has two or more windows; the pause or resume button
        while the selected rows have an agent session that
        a click would pause or resume. The Save and Restore buttons always show, each grayed out
        while it has nothing to act on (no session that can be saved; nothing saved), with a
        tooltip that says so."""
        agent_session_entries = self.list_controller.list_agent_session_entries()
        sessions_to_pause, sessions_to_resume = split_agent_sessions_into_pausable_and_resumable(
            self.list_controller.list_agent_session_entries_of_the_selected_rows())
        sessions_to_save, save_sessions_tool_tip = self.choose_what_the_save_button_saves_now()
        snapshot = self.list_controller.snapshot
        note, note_tool_tip, agent_tool_names_to_install_hooks_for = choose_hooks_note(
            snapshot.hooks_status_per_agent_tool if snapshot is not None else (),
            {entry.agent_tool_name for entry in agent_session_entries})
        bars_state = (EXPAND_OR_COLLAPSE_ALL_LOOKS[self.list_controller.is_fully_expanded()],
                      self.list_controller.count_window_rows() < 2,
                      PAUSE_OR_RESUME_SELECTED_ROWS_LOOKS["pause" if sessions_to_pause else "resume"],
                      not (sessions_to_pause or sessions_to_resume), note, note_tool_tip, list(agent_tool_names_to_install_hooks_for),
                      bool(sessions_to_save), save_sessions_tool_tip, bool(self.list_controller.owner.has_saved_agent_sessions()))
        if bars_state == self.shown_bars_state:
            return
        self.shown_bars_state = bars_state
        (expand_or_collapse_all_look, expand_or_collapse_all_button_is_hidden, pause_or_resume_selected_rows_look,
         pause_or_resume_selected_rows_button_is_hidden, _, _, self.agent_tool_names_to_install_hooks_for,
         there_are_sessions_to_save, _, sessions_are_saved) = bars_state
        expand_or_collapse_all_button_was_hidden = self.expand_or_collapse_all_button.isHidden()
        show_symbol_on_button(self.expand_or_collapse_all_button, *expand_or_collapse_all_look)
        self.expand_or_collapse_all_button.setToolTip_(expand_or_collapse_all_look[2])
        self.expand_or_collapse_all_button.setHidden_(expand_or_collapse_all_button_is_hidden)
        show_symbol_on_button(self.pause_or_resume_selected_rows_button, *pause_or_resume_selected_rows_look)
        self.pause_or_resume_selected_rows_button.setToolTip_(pause_or_resume_selected_rows_look[2])
        self.pause_or_resume_selected_rows_button.setHidden_(pause_or_resume_selected_rows_button_is_hidden)
        self.save_sessions_button.setEnabled_(there_are_sessions_to_save)
        self.save_sessions_button.setToolTip_(save_sessions_tool_tip)
        self.restore_sessions_button.setEnabled_(sessions_are_saved)
        self.restore_sessions_button.setToolTip_(RESTORE_SESSIONS_BUTTON_TOOL_TIP if sessions_are_saved else NO_SESSIONS_ARE_SAVED_TOOL_TIP)
        if self.expand_or_collapse_all_button.isHidden() != expand_or_collapse_all_button_was_hidden:
            self.fit_top_bar_to_width()
        self.hooks_note_text_field.setStringValue_(note)
        self.hooks_note_text_field.setToolTip_(note_tool_tip)
        self.fit_bottom_bar_to_width()

    @objc.python_method
    def fit_bottom_bar_to_width(self):
        """Shows the legend of the rows' outlines, after the Settings button at the bottom left, only
        whole: where it ends before the buttons at the bottom right (the Restore button, the
        leftmost of them, which always shows) and, while there
        is a hooks note, where the whole note and the Install Hooks button it needs fit after it
        too. The note asks for something to be done and the legend only explains, so in a narrower
        bar the legend hides and the note takes its place.

        Puts the hooks note after the legend, or after the Settings button where the legend is
        hidden, then its Install Hooks
        button (when there is something to install and room for it before the buttons at the
        right, which it would otherwise cover in a narrow sidebar), the note cut short
        before the button and the buttons at the right."""
        note = self.hooks_note_text_field.stringValue()
        note_size = self.hooks_note_text_field.fittingSize()
        button_width, button_height = self.install_hooks_button.frame().size
        right_end_x = self.restore_sessions_button.frame().origin.x - GAP_BETWEEN_TOP_BAR_BUTTONS
        legend_frame = self.row_outlines_legend_view.frame()
        legend_right_end_x = legend_frame.origin.x + legend_frame.size.width
        room_for_the_whole_note_and_its_button = (
            GAP_BEFORE_THE_HOOKS_NOTE + note_size.width
            + (GAP_BETWEEN_TOP_BAR_BUTTONS + button_width if self.agent_tool_names_to_install_hooks_for else 0)
            if note else 0)
        legend_shows = legend_right_end_x + room_for_the_whole_note_and_its_button <= right_end_x
        self.row_outlines_legend_view.setHidden_(not legend_shows)
        self.hooks_note_text_field.setHidden_(not note)
        if not note:
            self.install_hooks_button.setHidden_(True)
            return
        settings_button_frame = self.settings_button.frame()
        note_x = (legend_right_end_x if legend_shows
                  else settings_button_frame.origin.x + settings_button_frame.size.width) + GAP_BEFORE_THE_HOOKS_NOTE
        shows_install_button = (bool(self.agent_tool_names_to_install_hooks_for)
                                and note_x + GAP_BETWEEN_TOP_BAR_BUTTONS + button_width <= right_end_x)
        self.install_hooks_button.setHidden_(not shows_install_button)
        if shows_install_button:
            right_end_x -= button_width + GAP_BETWEEN_TOP_BAR_BUTTONS
        note_width = max(min(note_size.width, right_end_x - note_x), 0)
        self.hooks_note_text_field.setFrame_(NSMakeRect(
            note_x, self.bottom_bar_text_baseline_y - measure_text_baseline_y_in_its_frame(self.hooks_note_text_field),
            note_width, note_size.height))
        self.install_hooks_button.setFrameOrigin_((note_x + note_width + GAP_BETWEEN_TOP_BAR_BUTTONS,
                                                   (BOTTOM_BAR_HEIGHT - button_height) / 2))

    def installHooksClicked_(self, sender):
        self.list_controller.owner.install_hooks(self.agent_tool_names_to_install_hooks_for)

    def listSortOrderChosen_(self, sender):
        self.list_controller.owner.list_sort_order_chosen(read_popup_choice(sender), False)  # in its first direction

    @objc.python_method
    def open_the_menu_of_the_hidden_sort_popup(self):
        """Opens the Sort pop-up's own menu with its chosen order at the top left of the pop-up's
        place, the left end of the top bar, as the pop-up opens it while it shows, and returns
        once the menu closes. The menu's items are the pop-up's, so an order chosen there is
        selected in the pop-up and reported like one chosen in the pop-up (listSortOrderChosen_)."""
        popup_frame = self.list_sort_order_popup.frame()
        self.list_sort_order_popup.menu().popUpMenuPositioningItem_atLocation_inView_(
            self.list_sort_order_popup.selectedItem(), (popup_frame.origin.x, popup_frame.origin.y + popup_frame.size.height),
            self.view())

    @objc.python_method
    def show_list_sort_order(self, list_sort_order, is_reversed):
        """Shows the rows in this order (reversed when is_reversed) and the pop-up with it chosen
        (the dropdown, the sidebar and the list window share one choice)."""
        self.view()
        select_popup_choice(self.list_sort_order_popup, list_sort_order)
        self.list_controller.set_list_sort_order(list_sort_order, is_reversed)

    def idleTabsButtonClicked_(self, sender):
        self.list_controller.owner.idle_tabs_are_hidden_chosen(not self.list_controller.idle_tabs_are_hidden)

    @objc.python_method
    def show_idle_tabs_are_hidden(self, idle_tabs_are_hidden):
        """Hides or shows the idle tabs' rows, and gives the button the title of what a click then
        does (the dropdown, the sidebar and the list window share one choice)."""
        self.view()
        self.list_controller.set_idle_tabs_are_hidden(idle_tabs_are_hidden)
        self.update_idle_tabs_button()

    @objc.python_method
    def update_idle_tabs_button(self):
        title, tool_tip = IDLE_TABS_BUTTON_LOOKS[self.list_controller.idle_tabs_are_hidden]
        self.idle_tabs_button.setTitle_(title)
        self.idle_tabs_button.setToolTip_(tool_tip)

    @objc.python_method
    def put_the_keyboard_in_the_search_box(self):
        """Command-F or "/" in the list: puts the keyboard in the search box, its text selected.
        Returns whether it did (not while the box is hidden, in a sidebar too narrow for it)."""
        window = self.view().window()
        if window is None or self.search_field.isHiddenOrHasHiddenAncestor():
            return False
        window.makeFirstResponder_(self.search_field)
        return True

    def controlTextDidChange_(self, notification):
        """Filters the list as each letter is typed in the search box."""
        if notification.object() is self.search_field:
            self.filter_the_list_by_the_search_box_text()

    def searchFieldChanged_(self, sender):
        """The search box's own action: also sent when its button empties it, which typing doesn't do."""
        self.filter_the_list_by_the_search_box_text()

    @objc.python_method
    def filter_the_list_by_the_search_box_text(self):
        """The search box's text changed, typed or emptied by its button: the list shows that search."""
        self.list_controller.set_search_text(self.search_field.stringValue())
        self.let_an_empty_search_box_step_aside_for_a_message()

    @objc.python_method
    def empty_the_search_box(self):
        """The list emptied its search (clear_the_search): empties the search box with it."""
        self.search_field.setStringValue_("")
        self.let_an_empty_search_box_step_aside_for_a_message()

    @objc.python_method
    def let_an_empty_search_box_step_aside_for_a_message(self):
        """Fits the top bar again once the search box is empty beside a message: a box that stayed
        while the other controls stepped aside, because it held a search (lay_out_top_bar), steps
        aside too, and the message gets its room."""
        if not self.search_field.stringValue() and self.message_text_field.stringValue():
            self.fit_top_bar_to_width()

    def control_textView_doCommandBySelector_(self, control, text_view, command_selector):
        """The search box's keys: Return shows the selected row's tab, as in the list, once the rows
        are those of the text the box holds; Up and Down
        move the selection among the tab rows; Esc empties the box, and in an empty box closes
        the list as Esc in the list does. Other keys edit the text."""
        if control is not self.search_field:
            return False
        if command_selector == "insertNewline:":
            self.list_controller.show_the_selected_row_once_the_rows_show_the_search_box_search()
            return True
        if command_selector in ("moveUp:", "moveDown:"):
            self.list_controller.move_the_selection_among_tab_rows(-1 if command_selector == "moveUp:" else 1)
            return True
        if command_selector == "cancelOperation:":
            if self.search_field.stringValue():
                self.list_controller.clear_the_search()  # the box with it (empty_the_search_box)
            else:
                self.list_controller.owner.close_the_list_after_escape(self.list_controller)
            return True
        return False

    def caseSensitiveCheckboxClicked_(self, sender):
        self.list_controller.owner.search_is_case_sensitive_chosen(sender.state() == NSControlStateValueOn)

    @objc.python_method
    def show_search_is_case_sensitive(self, search_is_case_sensitive):
        """Checks or unchecks the "aA" checkbox and has the search count case, or not (the
        dropdown, the sidebar and the list window share one choice)."""
        self.view()
        self.case_sensitive_checkbox.setState_(NSControlStateValueOn if search_is_case_sensitive else NSControlStateValueOff)
        self.list_controller.set_search_is_case_sensitive(search_is_case_sensitive)

    def settingsClicked_(self, sender):
        self.list_controller.owner.open_settings()

    @objc.python_method
    def show_message(self, message, sort_control_and_hide_idle_step_aside=True):
        """Shows one gray line at the top (an empty message hides it); the app controller uses it
        when the app is about to restart, with no rows to sort or hide, and when a terminal can't
        be reached while another's rows stay listed, where the sort control and the Hide Idle
        button keep their places (sort_control_and_hide_idle_step_aside False) and the message
        takes the room left, its tooltip holding all of it."""
        self.view()
        self.sort_control_and_hide_idle_step_aside_for_the_message = sort_control_and_hide_idle_step_aside
        self.message_text_field.setStringValue_(message)
        self.message_text_field.setToolTip_(message)
        self.fit_top_bar_to_width()

    @objc.python_method
    def fit_size_to_rows(self, screen_the_panel_is_about_to_open_on=None):
        """Sizes the panel to its rows as if all were expanded, so collapsing or expanding rows
        keeps that size: as wide as the widest row's texts need (a process row's command doesn't
        count: it is cut short to the room the other rows leave it), from SMALLEST_POPOVER_WIDTH
        up to LARGEST_SHARE_OF_SCREEN_WIDTH of the screen's usable width, where a wider row is
        cut short to fit; and as tall as the rows, up to the height of the screen's usable area,
        where the list then scrolls up and down (it never scrolls sideways).

        Args:
            screen_the_panel_is_about_to_open_on: The NSScreen of the menu bar icon that a closed
                dropdown is about to open under: its panel isn't in a window on that screen yet, so
                it can't tell which screen's size limits it. None for an open panel, which is sized
                for its own window's screen.
        """
        window = self.view().window()
        screen = (screen_the_panel_is_about_to_open_on or (window.screen() if window is not None else None)
                  or NSScreen.screens()[0])
        usable_frame = screen.visibleFrame()
        bars_height = TOP_BAR_HEIGHT + BOTTOM_BAR_HEIGHT + self.list_controller.measure_column_titles_row_height()  # and the column titles
        list_height = self.list_controller.measure_fully_expanded_list_height()  # the list has no spacing between rows
        largest_list_height = usable_frame.size.height - bars_height - SPACE_FOR_POPOVER_ARROW_AND_MARGIN
        list_height = min(max(list_height + LIST_BOTTOM_PADDING, SMALLEST_LIST_HEIGHT), largest_list_height)
        width = self.list_controller.measure_widest_row_width() + SPACE_FOR_SCROLLER_AND_MARGINS
        width = min(max(width, SMALLEST_POPOVER_WIDTH), usable_frame.size.width * LARGEST_SHARE_OF_SCREEN_WIDTH)
        preferred_size = self.preferredContentSize()
        if (preferred_size.width, preferred_size.height) != (width, list_height + bars_height):  # it runs after every snapshot
            self.setPreferredContentSize_((width, list_height + bars_height))


def build_dropdown_popover(list_panel_controller):
    """Returns a transient NSPopover (it closes when you click elsewhere) showing the panel."""
    popover = NSPopover.alloc().init()
    popover.setContentViewController_(list_panel_controller)
    popover.setBehavior_(NSPopoverBehaviorTransient)
    popover.setAnimates_(False)
    return popover
