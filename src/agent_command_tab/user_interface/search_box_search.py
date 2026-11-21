"""What the list's search box and the Saved sessions window's share: rows that follow a search
box's text once the core has answered which conversations hold it.

The rows' own controller knows which names hold the text. Which conversations do is asked of the
core, one request at a time, and the rows show a new text once the answer has come, or, when it
takes longer than SECONDS_THE_ROWS_WAIT_FOR_THE_CONVERSATION_MATCHES, with the name matches alone
until it does.
"""

from Foundation import NSTimer

# A new search text is shown once the core has answered which conversations hold it, which takes
# milliseconds; when it takes longer than this, the text is shown with its name matches alone,
# so the rows keep up with the typing (SearchBoxSearch.show_without_waiting_any_longer).
SECONDS_THE_ROWS_WAIT_FOR_THE_CONVERSATION_MATCHES = 0.5


class SearchBoxSearch:
    """A search box's text and "aA" choice (text, is_case_sensitive), and the search its rows show
    (shown_search), which follows them once it is known which conversations hold the text."""

    def __init__(self, make_search, ask_which_conversations_contain, show_search, on_new_search_of_the_search_box=None,
                 on_answer_for_the_search_box_search=None):
        """
        Args:
            make_search: Returns the search the rows show for (text, is_case_sensitive, the
                frozenset of the IDs whose conversations hold the text): the rows' own kind of
                search, with those as its text and is_case_sensitive (a namedtuple, compared with ==).
            ask_which_conversations_contain: Called with (text, is_case_sensitive, on_answer) to ask
                the core; on_answer takes the IDs whose conversations hold the text, or None when
                the core couldn't answer, on the main thread.
            show_search: Called with the search the rows show from now on, when it differs from
                the one they showed (shown_search, which is that search by then).
            on_new_search_of_the_search_box: Called, if given, when the search box's text or "aA"
                choice has changed its search, before anything is asked or shown for the new one.
            on_answer_for_the_search_box_search: Called, if given, after an answer for the search
                box's current text and "aA" choice was taken, whether or not it changed the rows.
        """
        self.make_search = make_search
        self.ask_which_conversations_contain = ask_which_conversations_contain
        self.show_search = show_search
        self.on_new_search_of_the_search_box = on_new_search_of_the_search_box
        self.on_answer_for_the_search_box_search = on_answer_for_the_search_box_search
        self.text = ""
        self.is_case_sensitive = False
        self.shown_search = make_search("", False, frozenset())
        # Shows the search box's search without the conversation matches, if the core hasn't
        # answered by then (show_without_waiting_any_longer).
        self.answer_wait_timer = None
        self.request_is_under_way = False  # one request to the core at a time
        self.request_is_due_again = False  # the search, or what it looks through, changed while one was under way

    def set_text(self, text):
        """Takes the search box's text, shown once the core has answered for it (show_once_the_core_answers)."""
        if text == self.text:
            return
        self.text = text
        self.show_once_the_core_answers()

    def set_is_case_sensitive(self, is_case_sensitive):
        """Has the search count case from now on, or not (the "aA" checkbox), shown as a new text is."""
        if is_case_sensitive == self.is_case_sensitive:
            return
        self.is_case_sensitive = is_case_sensitive
        if self.text:
            self.show_once_the_core_answers()

    def start_over(self):
        """Empties the text and the shown search without telling the rows, keeping the "aA" choice:
        for a search box and rows built anew. An answer still to come for the old text is left out."""
        self.stop_waiting_for_the_answer()
        self.text = ""
        self.shown_search = self.make_search("", self.is_case_sensitive, frozenset())

    def show_once_the_core_answers(self):
        """Asks the core which conversations hold the search box's text, and shows the search once
        it answers, or after SECONDS_THE_ROWS_WAIT_FOR_THE_CONVERSATION_MATCHES without its
        matches; an empty search box's at once, since it needs no answer."""
        if self.on_new_search_of_the_search_box is not None:
            self.on_new_search_of_the_search_box()
        self.stop_waiting_for_the_answer()
        if not self.text:
            self.show(self.make_search("", self.is_case_sensitive, frozenset()))
            return
        self.answer_wait_timer = NSTimer.scheduledTimerWithTimeInterval_repeats_block_(
            SECONDS_THE_ROWS_WAIT_FOR_THE_CONVERSATION_MATCHES, False, lambda timer: self.answer_wait_timer_fired())
        self.ask_which_conversations_match()

    def stop_waiting_for_the_answer(self):
        if self.answer_wait_timer is not None:
            self.answer_wait_timer.invalidate()
            self.answer_wait_timer = None

    def answer_wait_timer_fired(self):
        self.answer_wait_timer = None
        self.show_without_waiting_any_longer()

    def show_without_waiting_any_longer(self):
        """Shows the search box's search with the name matches alone when the core still hasn't
        answered for it (a busy core, or files it reads for the first time), so the rows keep up
        with the typing; its answer adds the conversation matches when it comes."""
        if not self.rows_show_the_search_box_search():
            self.show(self.make_search(self.text, self.is_case_sensitive, frozenset()))

    def rows_show_the_search_box_search(self):
        """Whether the rows show the search box's text, with its "aA" choice, rather than still
        the text it held before, whose rows stay until the core answers for the new one
        (show_once_the_core_answers). Case doesn't count for an empty text."""
        return (self.shown_search.text == self.text
                and (not self.text or self.shown_search.is_case_sensitive == self.is_case_sensitive))

    def show(self, search):
        """Has the rows show this search from now on, telling them when it differs from the one they show."""
        if search == self.shown_search:
            return
        self.shown_search = search
        self.show_search(search)

    def ask_which_conversations_match(self):
        """Asks the core which conversations hold the search box's text, while it holds any; one
        request at a time, and one more after it when the text, the case choice, or what the
        search looks through changed meanwhile (a caller asks again for the last). The answer
        comes in answer_arrived."""
        if not self.text:
            return
        if self.request_is_under_way:
            self.request_is_due_again = True
            return
        self.request_is_under_way = True
        asked_for = (self.text, self.is_case_sensitive)
        self.ask_which_conversations_contain(self.text, self.is_case_sensitive, lambda ids: self.answer_arrived(asked_for, ids))

    def answer_arrived(self, asked_for, ids):
        """Takes the core's answer to ask_which_conversations_match (on the main thread): the IDs
        whose conversations hold asked_for's text, or None when the core couldn't answer, which
        shows the search with the name matches alone. An answer for the search box's current
        search is shown; one for an earlier text is left out, and the current one asked."""
        self.request_is_under_way = False
        asked_for_the_search_box_search = asked_for == (self.text, self.is_case_sensitive)
        if asked_for_the_search_box_search:
            self.stop_waiting_for_the_answer()
            self.show(self.make_search(*asked_for, frozenset(ids or ())))
            if self.on_answer_for_the_search_box_search is not None:
                self.on_answer_for_the_search_box_search()
        if self.request_is_due_again or not asked_for_the_search_box_search:
            self.request_is_due_again = False
            self.ask_which_conversations_match()
