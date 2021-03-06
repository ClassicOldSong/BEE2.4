import utils
import tk_tools
if __name__ == '__main__':
    utils.fix_cur_directory()
    LOGGER = utils.init_logging(
        '../logs/compiler_pane.log',
        __name__,
        on_error=tk_tools.on_error
    )
    utils.setup_localisations(LOGGER)
else:
    LOGGER = utils.getLogger(__name__)


from tkinter import *
from tk_tools import TK_ROOT, FileField
from tkinter import ttk
from tkinter import filedialog

from functools import partial
from PIL import Image, ImageTk

import os.path

import img as png

from BEE2_config import ConfigFile, GEN_OPTS
from tooltip import add_tooltip
import SubPane

# The size of PeTI screenshots
PETI_WIDTH = 555
PETI_HEIGHT = 312

CORRIDOR_COUNTS = [
    ('sp_entry', 7),
    ('sp_exit', 4),
    ('coop', 4),
]

COMPILE_DEFAULTS = {
    'Screenshot': {
        'Type': 'AUTO',
        'Loc': '',
    },
    'General': {
        'spawn_elev': 'True',
        'player_model': 'PETI',
        'force_final_light': '0',
        'use_voice_priority': '1',
        'packfile_dump_dir': '',
        'packfile_dump_enable': '0',
    },
    'Corridor': {
        'sp_entry': '1',
        'sp_exit': '1',
        'coop': '1',
    },
    'Counts': {
        'brush': '0',
        'overlay': '0',

        'entity': '0',

        'max_brush': '8192',
        'max_overlay': '512',
        'max_entity': '2048',
    },
    'CorridorNames': {
        '{}_{}'.format(group, i): '{}: Corridor'.format(i)
        for group, length in CORRIDOR_COUNTS
        for i in range(1,length + 1)
    }
}

PLAYER_MODELS = {
    'ATLAS': _('ATLAS'),
    'PBODY': _('P-Body'),
    'SP': _('Chell'),
    'PETI': _('Bendy'),
}
PLAYER_MODEL_ORDER = ['PETI', 'SP', 'ATLAS', 'PBODY']
PLAYER_MODELS_REV = {value: key for key, value in PLAYER_MODELS.items()}

COMPILE_CFG = ConfigFile('compile.cfg')
COMPILE_CFG.set_defaults(COMPILE_DEFAULTS)
window = None
UI = {}

chosen_thumb = StringVar(
    value=COMPILE_CFG.get_val('Screenshot', 'Type', 'AUTO')
)
tk_screenshot = None  # The preview image shown

# Location we copy custom screenshots to
SCREENSHOT_LOC = os.path.abspath(os.path.join(
    os.getcwd(),
    '..',
    'config',
    'screenshot.jpg'
))

VOICE_PRIORITY_VAR = IntVar(
    value=COMPILE_CFG.get_bool('General', 'use_voice_priority', True)
)

player_model_var = StringVar(
    value=PLAYER_MODELS.get(
        COMPILE_CFG.get_val('General', 'player_model', 'PETI'),
        PLAYER_MODELS['PETI'],
    )
)
start_in_elev = IntVar(
    value=COMPILE_CFG.get_bool('General', 'spawn_elev')
)
cust_file_loc = COMPILE_CFG.get_val('Screenshot', 'Loc', '')
cust_file_loc_var = StringVar(value='')

packfile_dump_enable = IntVar(
    value=COMPILE_CFG.get_bool('General', 'packfile_dump_enable')
)

count_brush = IntVar(value=0)
count_entity = IntVar(value=0)
count_overlay = IntVar(value=0)

# Controls flash_count()
count_brush.should_flash = False
count_entity.should_flash = False
count_overlay.should_flash = False

# The data for the 3 progress bars -
# (variable, config_name, default_max, description)
COUNT_CATEGORIES = [
    (
        count_brush, 'brush', 8192,
        # i18n: Progress bar description
        _("Brushes form the walls or other parts of the test chamber. If this "
          "is high, it may help to reduce the size of the map or remove "
          "intricate shapes.")
    ),
    (
        count_entity, 'entity', 2048,
        # i18n: Progress bar description
        _("Entities are the things in the map that have functionality. Removing "
          "complex moving items will help reduce this. Items have their entity "
          "count listed in the item description window.\n\n"
          "This isn't totally accurate, some entity types are counted here "
          "but don't affect the ingame limit. "),
    ),
    (
        count_overlay, 'overlay', 512,
        # i18n: Progress bar description
        _("Overlays are smaller images affixed to surfaces, like signs or "
          "indicator lights. Hiding long antlines or setting them to signage "
          "will reduce this.")
    ),
]

vrad_light_type = IntVar(
    value=COMPILE_CFG.get_bool('General', 'vrad_force_full')
)
cleanup_screenshot = IntVar(
    value=COMPILE_CFG.get_bool('Screenshot', 'del_old', True)
)

CORRIDOR = {}


def save_corridors():
    """Save corridor names to the config file."""
    corridor_conf = COMPILE_CFG['CorridorNames']
    for group, corr in CORRIDOR.items():
        for i, name in enumerate(corr[1:], start=1):
            corridor_conf['{}_{}'.format(group, i)] = name


def load_corridors():
    corridor_conf = COMPILE_CFG['CorridorNames']
    for group, length in CORRIDOR_COUNTS:
        CORRIDOR[group] = ['Random'] + [
            corridor_conf['{}_{}'.format(group, i)]
            for i in
            range(1, length + 1)
        ]


def set_corr_values(group_name, props):
    """Set the corrdors according to the passed prop_block."""
    count = 7 if group_name == 'sp_entry' else 4
    group = CORRIDOR[group_name] = [_('Random')] + [
        # Note: default corridor description
        str(i) + ': ' + _('Corridor')
        for i in
        range(1, count + 1)
    ]
    for prop in props[group_name]:
        try:
            ind = int(prop.name)
        except ValueError:
            continue

        if 0 < ind <= count:
            group[ind] = '{!r}: {}'.format(ind, prop.value)


def make_corr_combo(frm, corr_name, width):
    widget = ttk.Combobox(
        frm,
        values=CORRIDOR[corr_name],
        width=width,
        exportselection=0,
    )
    widget['postcommand'] = partial(set_corr_dropdown, corr_name, widget)
    widget.state(['readonly'])
    widget.bind(
        '<<ComboboxSelected>>',
        partial(set_corr, corr_name)
    )
    widget.current(COMPILE_CFG.get_int('Corridor', corr_name))
    return widget


def flash_count():
    """Flash the counter between 0 and 100 when on."""
    should_cont = False

    for var in (count_brush, count_entity, count_overlay):
        if not var.should_flash:
            continue  # Abort when it shouldn't be flashing

        if var.get() == 0:
            var.set(100)
        else:
            var.set(0)

        should_cont = True

    if should_cont:
        TK_ROOT.after(750, flash_count)


def refresh_counts(reload=True):
    if reload:
        COMPILE_CFG.load()

    # Don't re-run the flash function if it's already on.
    run_flash = not (
        count_entity.should_flash or
        count_overlay.should_flash or
        count_brush.should_flash
    )

    for bar_var, name, default, tip_blurb in COUNT_CATEGORIES:
        value = COMPILE_CFG.get_int('Counts', name)

        if name == 'entity':
            # The in-engine entity limit is different to VBSP's limit
            # (that one might include prop_static, lights etc).
            max_value = default
        else:
            # Use or to ensure no divide-by-zero occurs..
            max_value = COMPILE_CFG.get_int('Counts', 'max_' + name) or default

        # If it's hit the limit, make it continously scroll to draw
        # attention to the bar.
        if value >= max_value:
            bar_var.should_flash = True
        else:
            bar_var.should_flash = False
            bar_var.set(100 * value / max_value)

        UI['count_' + name].tooltip_text = '{}/{} ({:.2%}):\n{}'.format(
            value,
            max_value,
            value / max_value,
            tip_blurb,
        )

    if run_flash:
        flash_count()


def set_pack_dump_dir(path):
    COMPILE_CFG['General']['packfile_dump_dir'] = path
    COMPILE_CFG.save()


def set_pack_dump_enabled():
    is_enabled = packfile_dump_enable.get()
    COMPILE_CFG['General']['packfile_dump_enable'] = str(is_enabled)
    COMPILE_CFG.save_check()

    if is_enabled:
        UI['packfile_filefield'].grid()
    else:
        UI['packfile_filefield'].grid_remove()


def find_screenshot(e=None):
    """Prompt to browse for a screenshot."""
    file_name = filedialog.askopenfilename(
        title='Find Screenshot',
        filetypes=[
            # note: File type description
            (_('Image Files'), '*.jpg *.jpeg *.jpe *.jfif *.png *.bmp'
                              '*.tiff *.tga *.ico *.psd'),
        ],
        initialdir='C:',
    )
    if file_name:
        load_screenshot(file_name)
    COMPILE_CFG.save_check()


def set_screen_type():
    """Set the type of screenshot used."""
    chosen = chosen_thumb.get()
    COMPILE_CFG['Screenshot']['type'] = chosen
    if chosen == 'CUST':
        UI['thumb_label'].grid(row=2, column=0, columnspan=2, sticky='EW')
    else:
        UI['thumb_label'].grid_forget()
    UI['thumb_label'].update()
    # Resize the pane to accommodate the shown/hidden image
    window.geometry('{}x{}'.format(
        window.winfo_width(),
        window.winfo_reqheight(),
    ))

    COMPILE_CFG.save()


def load_screenshot(path):
    """Copy the selected image, changing format if needed."""
    img = Image.open(path)
    COMPILE_CFG['Screenshot']['LOC'] = SCREENSHOT_LOC
    img.save(SCREENSHOT_LOC)
    set_screenshot(img)


def set_screenshot(img=None):
    # Make the visible screenshot small
    global tk_screenshot
    if img is None:
        try:
            img = Image.open(SCREENSHOT_LOC)
        except IOError:  # Image doesn't exist!
            # In that case, use a black image
            img = Image.new('RGB', (1, 1), color=(0, 0, 0))
    # Make a smaller image for showing in the UI..
    tk_img = img.resize(
        (
            int(PETI_WIDTH // 3.5),
            int(PETI_HEIGHT // 3.5),
        ),
        Image.LANCZOS
    )
    tk_screenshot = ImageTk.PhotoImage(tk_img)
    UI['thumb_label']['image'] = tk_screenshot


def set_model(e=None):
    """Save the selected player model."""
    text = player_model_var.get()
    COMPILE_CFG['General']['player_model'] = PLAYER_MODELS_REV[text]
    COMPILE_CFG.save()


def set_corr(corr_name, e):
    """Save the chosen corridor when it's changed.

    This is shared by all three dropdowns.
    """
    COMPILE_CFG['Corridor'][corr_name] = str(e.widget.current())
    COMPILE_CFG.save()


def set_corr_dropdown(corr_name, widget):
    """Set the values in the dropdown when it's opened."""
    widget['values'] = CORRIDOR[corr_name]


def make_setter(section, config, variable):
    """Create a callback which sets the given config from a variable."""

    def callback():
        COMPILE_CFG[section][config] = str(variable.get())
        COMPILE_CFG.save_check()

    return callback


def make_widgets():
    """Create the compiler options pane.

    """
    ttk.Label(window, justify='center', text=_(
        "Options on this panel can be changed \n"
        "without exporting or restarting the game."
    )).grid(row=0, column=0, sticky=EW, padx=2, pady=2)

    UI['nbook'] = nbook = ttk.Notebook(window)

    nbook.grid(row=1, column=0, sticky=NSEW)
    window.columnconfigure(0, weight=1)
    window.rowconfigure(1, weight=1)

    nbook.enable_traversal()

    map_frame = ttk.Frame(nbook)
    # note: Tab name
    nbook.add(map_frame, text=_('Map Settings'))
    make_map_widgets(map_frame)

    comp_frame = ttk.Frame(nbook)
    # note: Tab name
    nbook.add(comp_frame, text=_('Compile Settings'))
    make_comp_widgets(comp_frame)


def make_comp_widgets(frame: ttk.Frame):
    """Create widgets for the compiler settings pane.

    These are generally things that are aesthetic, and to do with the file and
    compilation process.
    """
    frame.columnconfigure(0, weight=1)

    thumb_frame = ttk.LabelFrame(
        frame,
        text=_('Thumbnail'),
        labelanchor=N,
    )
    thumb_frame.grid(row=0, column=0, sticky=EW)
    thumb_frame.columnconfigure(0, weight=1)

    UI['thumb_auto'] = ttk.Radiobutton(
        thumb_frame,
        text=_('Auto'),
        value='AUTO',
        variable=chosen_thumb,
        command=set_screen_type,
    )

    UI['thumb_peti'] = ttk.Radiobutton(
        thumb_frame,
        text=_('PeTI'),
        value='PETI',
        variable=chosen_thumb,
        command=set_screen_type,
    )

    UI['thumb_custom'] = ttk.Radiobutton(
        thumb_frame,
        text=_('Custom:'),
        value='CUST',
        variable=chosen_thumb,
        command=set_screen_type,
    )

    UI['thumb_label'] = ttk.Label(
        thumb_frame,
        anchor=CENTER,
        cursor=utils.CURSORS['link'],
    )
    UI['thumb_label'].bind(
        utils.EVENTS['LEFT'],
        find_screenshot,
    )

    UI['thumb_cleanup'] = ttk.Checkbutton(
        thumb_frame,
        text=_('Cleanup old screenshots'),
        variable=cleanup_screenshot,
        command=make_setter('Screenshot', 'del_old', cleanup_screenshot),
    )

    UI['thumb_auto'].grid(row=0, column=0, sticky='W')
    UI['thumb_peti'].grid(row=0, column=1, sticky='W')
    UI['thumb_custom'].grid(row=1, column=0, columnspan=2, sticky='NEW')
    UI['thumb_cleanup'].grid(row=3, columnspan=2, sticky='W')
    add_tooltip(
        UI['thumb_auto'],
        _("Override the map image to use a screenshot automatically taken "
          "from the beginning of a chamber. Press F5 to take a new "
          "screenshot. If the map has not been previewed recently "
          "(within the last few hours), the default PeTI screenshot "
          "will be used instead.")
    )
    add_tooltip(
        UI['thumb_peti'],
        _("Use the normal editor view for the map preview image.")
    )
    custom_tooltip = _(
        "Use a custom image for the map preview image. Click the "
        "screenshot to select.\n"
        "Images will be converted to JPEGs if needed."
    )
    add_tooltip(
        UI['thumb_custom'],
        custom_tooltip,
    )

    add_tooltip(
        UI['thumb_label'],
        custom_tooltip,
    )

    add_tooltip(
        UI['thumb_cleanup'],
        _('Automatically delete unused Automatic screenshots. '
          'Disable if you want to keep things in "portal2/screenshots". ')
    )

    if chosen_thumb.get() == 'CUST':
        # Show this if the user has set it before
        UI['thumb_label'].grid(row=2, column=0, columnspan=2, sticky='EW')
    set_screenshot()  # Load the last saved screenshot

    vrad_frame = ttk.LabelFrame(
        frame,
        text=_('Lighting:'),
        labelanchor=N,
    )
    vrad_frame.grid(row=1, column=0, sticky=EW)

    UI['light_fast'] = ttk.Radiobutton(
        vrad_frame,
        text=_('Fast'),
        value=0,
        variable=vrad_light_type,
        command=make_setter('General', 'vrad_force_full', vrad_light_type),
    )
    UI['light_fast'].grid(row=0, column=0)
    UI['light_full'] = ttk.Radiobutton(
        vrad_frame,
        text=_('Full'),
        value=1,
        variable=vrad_light_type,
        command=make_setter('General', 'vrad_force_full', vrad_light_type),
    )
    UI['light_full'].grid(row=0, column=1)

    add_tooltip(
        UI['light_fast'],
        _("Compile with lower-quality, fast lighting. This speeds "
          "up compile times, but does not appear as good. Some "
          "shadows may appear wrong.\n"
          "When publishing, this is ignored.")
    )
    add_tooltip(
        UI['light_full'],
        _("Compile with high-quality lighting. This looks correct, "
          "but takes longer to compute. Use if you're arranging lights. "
          "When publishing, this is always used.")
    )

    packfile_enable = ttk.Checkbutton(
        frame,
        text=_('Dump packed files to:'),
        variable=packfile_dump_enable,
        command=set_pack_dump_enabled,
    )

    packfile_frame = ttk.LabelFrame(
        frame,
        labelwidget=packfile_enable,
    )
    packfile_frame.grid(row=2, column=0, sticky=EW)

    UI['packfile_filefield'] = packfile_filefield = FileField(
        packfile_frame,
        is_dir=True,
        loc=COMPILE_CFG.get_val('General', 'packfile_dump_dir', ''),
        callback=set_pack_dump_dir,
    )
    packfile_filefield.grid(row=0, column=0, sticky=EW)
    packfile_frame.columnconfigure(0, weight=1)
    ttk.Frame(packfile_frame).grid(row=1)

    set_pack_dump_enabled()

    add_tooltip(
        packfile_enable,
        _("When compiling, dump all files which were packed into the map. Useful"
          " if you're intending to edit maps in Hammer.")
    )

    count_frame = ttk.LabelFrame(
        frame,
        text=_('Last Compile:'),
        labelanchor=N,
    )

    count_frame.grid(row=7, column=0, sticky=EW)
    count_frame.columnconfigure(0, weight=1)
    count_frame.columnconfigure(2, weight=1)

    ttk.Label(
        count_frame,
        text=_('Entity'),
        anchor=N,
    ).grid(row=0, column=0, columnspan=3, sticky=EW)

    UI['count_entity'] = ttk.Progressbar(
        count_frame,
        maximum=100,
        variable=count_entity,
        length=120,
    )
    UI['count_entity'].grid(
        row=1,
        column=0,
        columnspan=3,
        sticky=EW,
        padx=5,
    )

    ttk.Label(
        count_frame,
        text=_('Overlay'),
        anchor=CENTER,
    ).grid(row=2, column=0, sticky=EW)
    UI['count_overlay'] = ttk.Progressbar(
        count_frame,
        maximum=100,
        variable=count_overlay,
        length=50,
    )
    UI['count_overlay'].grid(row=3, column=0, sticky=EW, padx=5)

    UI['refresh_counts'] = SubPane.make_tool_button(
        count_frame,
        png.png('icons/tool_sub', resize_to=16),
        refresh_counts,
    )
    UI['refresh_counts'].grid(row=3, column=1)
    add_tooltip(
        UI['refresh_counts'],
        _("Refresh the compile progress bars. Press after a compile has been "
          "performed to show the new values."),
    )

    ttk.Label(
        count_frame,
        text=_('Brush'),
        anchor=CENTER,
    ).grid(row=2, column=2, sticky=EW)
    UI['count_brush'] = ttk.Progressbar(
        count_frame,
        maximum=100,
        variable=count_brush,
        length=50,
    )
    UI['count_brush'].grid(row=3, column=2, sticky=EW, padx=5)

    for wid_name in ('count_overlay', 'count_entity', 'count_brush'):
        # Add in tooltip logic to the widgets.
        add_tooltip(UI[wid_name])

    refresh_counts(reload=False)


def make_map_widgets(frame: ttk.Frame):
    """Create widgets for the map settings pane.

    These are things which mainly affect the geometry or gameplay of the map.
    """
    frame.columnconfigure(0, weight=1)

    voice_frame = ttk.LabelFrame(
        frame,
        text=_('Voicelines:'),
        labelanchor=NW,
    )
    voice_frame.grid(row=1, column=0, sticky=EW)

    UI['voice_priority'] = voice_priority = ttk.Checkbutton(
        voice_frame,
        text=_("Use voiceline priorities"),
        variable=VOICE_PRIORITY_VAR,
        command=make_setter('General', 'use_voice_priority', VOICE_PRIORITY_VAR),
    )
    voice_priority.grid(row=0, column=0)
    add_tooltip(
        voice_priority,
        _("Only choose the highest-priority voicelines. This means more "
          "generic lines will can only be chosen if few test elements are in "
          "the map. If disabled any applicable lines will be used."),
    )

    elev_frame = ttk.LabelFrame(
        frame,
        text=_('Spawn at:'),
        labelanchor=N,
    )

    elev_frame.grid(row=2, column=0, sticky=EW)
    elev_frame.columnconfigure(0, weight=1)
    elev_frame.columnconfigure(1, weight=1)

    UI['elev_preview'] = ttk.Radiobutton(
        elev_frame,
        text=_('Entry Door'),
        value=0,
        variable=start_in_elev,
        command=make_setter('General', 'spawn_elev', start_in_elev),
    )

    UI['elev_elevator'] = ttk.Radiobutton(
        elev_frame,
        text=_('Elevator'),
        value=1,
        variable=start_in_elev,
        command=make_setter('General', 'spawn_elev', start_in_elev),
    )

    UI['elev_preview'].grid(row=0, column=0, sticky=W)
    UI['elev_elevator'].grid(row=0, column=1, sticky=W)

    add_tooltip(
        UI['elev_elevator'],
        _("When previewing in SP, spawn inside the entry elevator. "
          "This also disables the map restarts when you reach the "
          "exit door. Use this to examine the entry and exit corridors.")
    )
    add_tooltip(
        UI['elev_preview'],
        _("When previewing in SP, spawn just before the entry door. "
          "When you reach the exit door, the map will restart.")
    )

    corr_frame = ttk.LabelFrame(
        frame,
        width=18,
        text=_('Corridor:'),
        labelanchor=N,
    )
    corr_frame.grid(row=3, column=0, sticky=EW)
    corr_frame.columnconfigure(0, weight=1)
    corr_frame.columnconfigure(1, weight=1)

    load_corridors()

    UI['corr_sp_entry'] = make_corr_combo(
        corr_frame,
        'sp_entry',
        width=9,
    )

    UI['corr_sp_exit'] = make_corr_combo(
        corr_frame,
        'sp_exit',
        width=9,
    )

    UI['corr_coop'] = make_corr_combo(
        corr_frame,
        'coop',
        width=9,
    )

    UI['corr_sp_entry'].grid(row=1, column=0, sticky=EW)
    UI['corr_sp_exit'].grid(row=1, column=1, sticky=EW)
    UI['corr_coop'].grid(row=2, column=1, sticky=EW)
    ttk.Label(
        corr_frame,
        text=_('SP Entry:'),
        anchor=CENTER,
    ).grid(row=0, column=0, sticky=EW)
    ttk.Label(
        corr_frame,
        text=_('SP Exit:'),
        anchor=CENTER,
    ).grid(row=0, column=1, sticky=EW)
    ttk.Label(
        corr_frame,
        text=_('Coop:'),
        anchor=CENTER,
    ).grid(row=2, column=0, sticky=EW)

    model_frame = ttk.LabelFrame(
        frame,
        text=_('Player Model (SP):'),
        labelanchor=N,
    )
    model_frame.grid(row=4, column=0, sticky=EW)
    UI['player_mdl'] = ttk.Combobox(
        model_frame,
        exportselection=0,
        textvariable=player_model_var,
        values=[PLAYER_MODELS[mdl] for mdl in PLAYER_MODEL_ORDER],
        width=20,
    )
    # Users can only use the dropdown
    UI['player_mdl'].state(['readonly'])
    UI['player_mdl'].grid(row=0, column=0, sticky=EW)

    UI['player_mdl'].bind('<<ComboboxSelected>>', set_model)
    model_frame.columnconfigure(0, weight=1)


def make_pane(tool_frame):
    """Initialise when part of the BEE2."""
    global window
    window = SubPane.SubPane(
        TK_ROOT,
        options=GEN_OPTS,
        title=_('Compile Options'),
        name='compiler',
        resize_x=True,
        resize_y=False,
        tool_frame=tool_frame,
        tool_img=png.png('icons/win_compiler'),
        tool_col=4,
    )
    window.columnconfigure(0, weight=1)
    window.rowconfigure(0, weight=1)
    make_widgets()


def init_application():
    """Initialise when standalone."""
    global window
    window = TK_ROOT
    window.title(_('Compiler Options - {}').format(utils.BEE_VERSION))
    window.resizable(True, False)

    make_widgets()


if __name__ == '__main__':
    # Run this standalone.

    init_application()

    TK_ROOT.deiconify()
    TK_ROOT.mainloop()
