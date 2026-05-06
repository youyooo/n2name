# ai-n2name — Community fork maintained by youyooo
# License: GPL-3.0-or-later
import bpy
import os
import json
from bpy.app.handlers import persistent

bl_info = {
    "name": "ai-N改名",
    "author": "youyooo",
    "version": (1, 3, 0),
    "blender": (4, 2, 0),
    "location": "3D视图 > 侧边栏 > N改名",
    "description": "N面板标签管理：改名、排序、锁位、自动按使用频率排序",
    "category": "界面",
}

original_data = {}
config_file = os.path.join(os.path.dirname(__file__), "n_panel.py")

# ── dwell-time tracking (panel stay duration) ──
_active_category = None
_dwell_interval = 2.0
_original_draws = {}
_polling_active = False

# ── system tab detection ──
SYSTEM_CATEGORIES = {"Item", "Tool", "View"}
SYSTEM_TAB_ORDER = {"Item": 0, "Tool": 1, "View": 2}

def is_system_category(category_name):
    """Check if a category name belongs to Blender's built-in panels."""
    return category_name in SYSTEM_CATEGORIES


# ── data persistence ──
def load_py_data():
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                content = f.read()
            namespace = {}
            exec(content, namespace)
            return namespace.get('config_data', {})
        except Exception:
            return {}
    return {}

def save_py_data(data):
    try:
        os.makedirs(os.path.dirname(config_file), exist_ok=True)
        with open(config_file, 'w', encoding='utf-8') as f:
            f.write("config_data = ")
            import pprint
            pprint.pprint(data, f, indent=2, width=80, sort_dicts=False)
    except Exception:
        pass

def get_click_counts():
    return load_py_data().get("click_counts", {})

def get_locked_categories():
    return load_py_data().get("locked_categories", [])

def increment_click_count(category_name):
    saved_data = load_py_data()
    click_counts = saved_data.get("click_counts", {})
    click_counts[category_name] = click_counts.get(category_name, 0) + 1
    saved_data["click_counts"] = click_counts
    save_py_data(saved_data)


# ── dwell-time: detect which N-panel tab the user is actually viewing ──
def make_draw_wrapper(original_draw):
    def wrapper(self, context):
        global _active_category
        cat = getattr(self.__class__, 'bl_category', '').strip()
        if cat:
            _active_category = cat
        return original_draw(self, context)
    return wrapper


def install_draw_trackers():
    global _original_draws, _active_category
    for cls in bpy.types.Panel.__subclasses__():
        if (hasattr(cls, 'bl_space_type') and
            hasattr(cls, 'bl_region_type') and
            cls.bl_space_type == 'VIEW_3D' and
            cls.bl_region_type == 'UI'):
            cls_name = cls.__name__
            if cls_name not in _original_draws:
                try:
                    _original_draws[cls_name] = cls.draw
                    cls.draw = make_draw_wrapper(cls.draw)
                except Exception:
                    pass


def remove_draw_trackers():
    global _original_draws
    for cls in bpy.types.Panel.__subclasses__():
        cls_name = cls.__name__
        if cls_name in _original_draws:
            try:
                cls.draw = _original_draws[cls_name]
            except Exception:
                pass
    _original_draws.clear()


_dwell_tick = 0
_DWELL_SAVE_EVERY = 15  # save to disk every ~30 s (15 ticks × 2 s)

def dwell_timer():
    global _active_category, _polling_active, _dwell_tick
    if not _polling_active:
        return  # stop timer
    if _active_category and bpy.context and hasattr(bpy.context, 'scene') and bpy.context.scene:
        for item in bpy.context.scene.npt_categories:
            if item.category_name == _active_category:
                item.dwell_time += _dwell_interval
                break
    _active_category = None
    _dwell_tick += 1
    if _dwell_tick >= _DWELL_SAVE_EVERY:
        _dwell_tick = 0
        try:
            save_changes_to_py()
        except Exception:
            pass
    return _dwell_interval


def start_dwell_timer():
    global _polling_active
    if not _polling_active:
        _polling_active = True
        bpy.app.timers.register(dwell_timer, first_interval=_dwell_interval, persistent=True)


def stop_dwell_timer():
    global _polling_active
    _polling_active = False
    if dwell_timer in bpy.app.timers.handlers:
        bpy.app.timers.unregister(dwell_timer)


# ── auto-sort ──
def _snapshot_items(scene):
    """Extract full data from collection before clearing (safe against RNA invalidation)."""
    snap = {}
    for it in scene.npt_categories:
        snap[it.category_name] = {
            'new_category': it.new_category,
            'order': it.order,
            'click_count': it.click_count,
            'dwell_time': it.dwell_time,
            'panel_count': it.panel_count,
            'is_locked': it.is_locked,
            'is_system': it.is_system,
        }
    return snap


def auto_sort_by_clicks(scene):
    """Sort non-system, non-locked categories by dwell_time + click_count descending."""
    locked = get_locked_categories()

    # snapshot before touching the collection (RNA references die on clear)
    snap = _snapshot_items(scene)

    items = []
    for it in scene.npt_categories:
        items.append(it.category_name)

    if not items:
        return

    # split into three groups using snapshot data
    system_items = [name for name in items if snap[name]['is_system']]
    locked_items = [name for name in items if snap[name]['is_locked'] and not snap[name]['is_system']]
    normal_items = [name for name in items if not snap[name]['is_system'] and not snap[name]['is_locked']]

    # sort normal by dwell_time desc, then click_count desc
    normal_items.sort(key=lambda x: (snap[x]['dwell_time'], snap[x]['click_count']), reverse=True)

    # system tabs ordered by SYSTEM_TAB_ORDER, then alphabetically
    system_items.sort(key=lambda x: SYSTEM_TAB_ORDER.get(x, 99))

    # locked keep their relative order
    ordered_names = system_items + locked_items + normal_items

    if items == ordered_names:
        return

    # rebuild collection order from snapshot
    scene.npt_categories.clear()
    for name in ordered_names:
        s = snap[name]
        item = scene.npt_categories.add()
        item.category_name = name
        item.new_category = s['new_category']
        item.order = s['order']
        item.click_count = s['click_count']
        item.dwell_time = s['dwell_time']
        item.panel_count = s['panel_count']
        item.is_locked = s['is_locked']
        item.is_system = s['is_system']


# ── operators ──
class NPT_OT_refresh_ui(bpy.types.Operator):
    bl_idname = "npt.refresh_ui"
    bl_label = "刷新列表"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        scene = context.scene

        # preserve in-memory dwell_time before clearing (config may be stale)
        dwell_snapshot = {}
        for it in scene.npt_categories:
            if it.dwell_time > 0:
                dwell_snapshot[it.category_name] = it.dwell_time

        scene.npt_categories.clear()

        # collect all VIEW_3D UI panels
        category_panels = {}
        for cls in bpy.types.Panel.__subclasses__():
            if (hasattr(cls, 'bl_space_type') and
                hasattr(cls, 'bl_region_type') and
                cls.bl_space_type == 'VIEW_3D' and
                cls.bl_region_type == 'UI'):

                cat = getattr(cls, 'bl_category', 'Tool').strip()
                if cat not in category_panels:
                    category_panels[cat] = {'count': 0, 'is_system': False}
                category_panels[cat]['count'] += 1

                if cls.__name__ not in original_data:
                    original_data[cls.__name__] = {
                        'category': cat,
                        'module': cls.__module__
                    }

                # detect system panels
                mod = cls.__module__
                if mod.startswith("bl_ui."):
                    category_panels[cat]['is_system'] = True

        # determine which categories are system
        for cat_name in category_panels:
            category_panels[cat_name]['is_system'] = is_system_category(cat_name)

        saved_data = load_py_data()
        category_order = saved_data.get("category_order", {})
        click_counts = saved_data.get("click_counts", {})
        config_dwell_times = saved_data.get("dwell_times", {})
        locked_categories = saved_data.get("locked_categories", [])

        # merge: in-memory dwell_time takes priority over config
        merged_dwell = {**config_dwell_times, **dwell_snapshot}

        # build sorted list
        entries = []
        for cat_name, info in category_panels.items():
            order = category_order.get(cat_name, 999)
            entries.append((order, cat_name, info))

        entries.sort(key=lambda x: x[0])

        for order, cat_name, info in entries:
            item = scene.npt_categories.add()
            item.category_name = cat_name
            item.new_category = cat_name
            item.panel_count = info['count']
            item.order = order
            item.click_count = click_counts.get(cat_name, 0)
            item.dwell_time = merged_dwell.get(cat_name, 0.0)
            item.is_locked = cat_name in locked_categories
            item.is_system = info['is_system'] or is_system_category(cat_name)

        # install draw trackers to detect active N-panel tab
        install_draw_trackers()

        if scene.npt_auto_sort:
            auto_sort_by_clicks(scene)
        return {'FINISHED'}


class NPT_OT_apply_tag(bpy.types.Operator):
    bl_idname = "npt.apply_tag"
    bl_label = "应用修改"
    bl_options = {'INTERNAL'}

    category_name: bpy.props.StringProperty()

    def execute(self, context):
        for item in context.scene.npt_categories:
            if item.category_name == self.category_name:
                new_category = item.new_category.strip()
                if new_category and new_category != item.category_name:
                    modified_count = modify_tag(item.category_name, new_category)
                    if modified_count > 0:
                        item.category_name = new_category
                        increment_click_count(new_category)
                        save_changes_to_py()
                        apply_saved_changes()
                        bpy.ops.npt.refresh_ui()
                break
        return {'FINISHED'}


class NPT_OT_re_tag(bpy.types.Operator):
    bl_idname = "npt.re_tag"
    bl_label = "还原所有修改"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        for panel_name, data in original_data.items():
            for cls in bpy.types.Panel.__subclasses__():
                if cls.__name__ == panel_name:
                    try:
                        bpy.utils.unregister_class(cls)
                        cls.bl_category = data['category']
                        bpy.utils.register_class(cls)
                    except:
                        pass
                    break
        for item in context.scene.npt_categories:
            for panel_name, data in original_data.items():
                if data['category'] == item.category_name:
                    item.category_name = data['category']
                    item.new_category = data['category']
                    break
        save_py_data({})
        bpy.ops.npt.refresh_ui()
        return {'FINISHED'}


class NPT_OT_move_category(bpy.types.Operator):
    bl_idname = "npt.move_category"
    bl_label = "移动"
    bl_options = {'INTERNAL'}

    direction: bpy.props.StringProperty()
    category_name: bpy.props.StringProperty()

    def execute(self, context):
        categories = context.scene.npt_categories
        idx = -1
        for i, item in enumerate(categories):
            if item.category_name == self.category_name:
                idx = i
                break
        if idx == -1:
            return {'CANCELLED'}

        if self.direction == 'UP' and idx > 0:
            # don't allow moving above system tabs
            above = categories[idx - 1]
            if above.is_system and not categories[idx].is_system:
                return {'CANCELLED'}
            categories.move(idx, idx - 1)
        elif self.direction == 'DOWN' and idx < len(categories) - 1:
            categories.move(idx, idx + 1)

        increment_click_count(self.category_name)

        if context.scene.npt_auto_sort:
            auto_sort_by_clicks(context.scene)

        save_changes_to_py()
        apply_saved_changes()
        return {'FINISHED'}


class NPT_OT_toggle_lock(bpy.types.Operator):
    bl_idname = "npt.toggle_lock"
    bl_label = "锁定/解锁标签"
    bl_options = {'INTERNAL'}

    category_name: bpy.props.StringProperty()

    def execute(self, context):
        saved_data = load_py_data()
        locked = saved_data.get("locked_categories", [])

        if self.category_name in locked:
            locked.remove(self.category_name)
        else:
            locked.append(self.category_name)

        saved_data["locked_categories"] = locked
        save_py_data(saved_data)

        for item in context.scene.npt_categories:
            if item.category_name == self.category_name:
                item.is_locked = self.category_name in locked
                break

        if context.scene.npt_auto_sort:
            auto_sort_by_clicks(context.scene)
            save_changes_to_py()
            apply_saved_changes()
        return {'FINISHED'}


class NPT_OT_toggle_auto_sort(bpy.types.Operator):
    bl_idname = "npt.toggle_auto_sort"
    bl_label = "切换自动排序"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        context.scene.npt_auto_sort = not context.scene.npt_auto_sort
        if context.scene.npt_auto_sort:
            auto_sort_by_clicks(context.scene)
            save_changes_to_py()
            apply_saved_changes()
        return {'FINISHED'}


# ── property group ──
class NPT_CategoryItem(bpy.types.PropertyGroup):
    category_name: bpy.props.StringProperty(name="类别名称")

    def update_new_category(self, context):
        name = self.category_name
        def callback():
            bpy.ops.npt.apply_tag('EXEC_DEFAULT', category_name=name)
            return None
        bpy.app.timers.register(callback, first_interval=0.1)

    new_category: bpy.props.StringProperty(
        name="新标签",
        update=update_new_category
    )
    order: bpy.props.IntProperty(name="顺序", default=0)
    click_count: bpy.props.IntProperty(name="点击数", default=0)
    dwell_time: bpy.props.FloatProperty(name="停留时间(秒)", default=0.0)
    panel_count: bpy.props.IntProperty(name="面板数", default=0)
    is_locked: bpy.props.BoolProperty(name="锁定", default=False)
    is_system: bpy.props.BoolProperty(name="系统标签", default=False)


# ── panel UI ──
class NPT_PT_panel_ui(bpy.types.Panel):
    bl_label = "面板标签管理器"
    bl_idname = "NPT_PT_panel_ui"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = "N改名"

    def draw(self, context):
        layout = self.layout
        scene = context.scene

        # header row
        row = layout.row()
        row.operator("npt.refresh_ui", text="刷新")
        row.operator("npt.re_tag", text="还原")
        sub = row.row(align=True)
        if scene.npt_auto_sort:
            sub.alert = True
        sub.operator("npt.toggle_auto_sort", text="", icon="SORTSIZE",
                     depress=scene.npt_auto_sort)
        if scene.npt_auto_sort:
            row = layout.row()
            row.label(text="自动排序已开启: 按停留时长降序", icon='INFO')

        items = scene.npt_categories
        if len(items) == 0:
            layout.label(text="点击刷新按钮加载面板列表")
            return

        # column headers
        box = layout.box()
        # ─ lock | index | name | rename | dwell | move ─
        row = box.row(align=True)
        row.label(text="", icon='LOCKED')
        row.label(text="#")
        row.label(text="标签名")
        row.label(text="新名")
        row.label(text="停留")
        row.label(text="排序")

        for idx, item in enumerate(items):
            row = box.row(align=True)
            row.alignment = 'EXPAND'

            # lock toggle
            lock_icon = 'LOCKED' if item.is_locked else 'UNLOCKED'
            op_lock = row.operator("npt.toggle_lock", text="", icon=lock_icon, emboss=False)
            op_lock.category_name = item.category_name

            # index
            row.label(text=str(idx + 1))

            # category name (dim if system)
            name_row = row.row()
            if item.is_system:
                name_row.enabled = False
                name_row.label(text=item.category_name)
            else:
                name_row.label(text=item.category_name)

            # rename input (disabled for system)
            rename_row = row.row()
            rename_row.alignment = 'EXPAND'
            if item.is_system:
                rename_row.enabled = False
            rename_row.prop(item, "new_category", text="")

            # dwell time (formatted)
            t = item.dwell_time
            if t >= 3600:
                label = f"{t/3600:.1f}h"
            elif t >= 60:
                label = f"{t/60:.1f}m"
            else:
                label = f"{t:.0f}s"
            row.label(text=label)

            # move buttons (disabled for system)
            move_row = row.row(align=True)
            if item.is_system:
                move_row.enabled = False
            op_up = move_row.operator("npt.move_category", text="", icon="TRIA_UP", emboss=False)
            op_up.direction = 'UP'
            op_up.category_name = item.category_name
            op_down = move_row.operator("npt.move_category", text="", icon="TRIA_DOWN", emboss=False)
            op_down.direction = 'DOWN'
            op_down.category_name = item.category_name


# ── tag manipulation ──
def get_p_tag(category_name):
    panels = []
    for cls in bpy.types.Panel.__subclasses__():
        if (hasattr(cls, 'bl_space_type') and
            hasattr(cls, 'bl_region_type') and
            hasattr(cls, 'bl_category') and
            cls.bl_space_type == 'VIEW_3D' and
            cls.bl_region_type == 'UI' and
            getattr(cls, 'bl_category', 'Tool').strip() == category_name):
            panels.append(cls)
    return panels

def modify_tag(category_name, new_category):
    panels = get_p_tag(category_name)
    if not panels:
        return 0
    modified_count = 0
    for panel_class in panels:
        panel_name = panel_class.__name__
        if panel_name not in original_data:
            original_data[panel_name] = {
                'category': panel_class.bl_category,
                'module': panel_class.__module__
            }
        try:
            bpy.utils.unregister_class(panel_class)
        except:
            continue
        panel_class.bl_category = new_category
        try:
            bpy.utils.register_class(panel_class)
            modified_count += 1
        except:
            panel_class.bl_category = original_data[panel_name]['category']
            try:
                bpy.utils.register_class(panel_class)
            except:
                pass
    return modified_count

def save_changes_to_py():
    all_panels = {}
    for cls in bpy.types.Panel.__subclasses__():
        if (hasattr(cls, 'bl_space_type') and
            hasattr(cls, 'bl_region_type') and
            cls.bl_space_type == 'VIEW_3D' and
            cls.bl_region_type == 'UI'):
            panel_name = cls.__name__
            current_category = getattr(cls, 'bl_category', 'Tool')
            all_panels[panel_name] = current_category

    category_order = {}
    click_counts = {}
    dwell_times = {}
    if bpy.context and hasattr(bpy.context, 'scene'):
        for idx, item in enumerate(bpy.context.scene.npt_categories):
            category_order[item.category_name] = idx
            click_counts[item.category_name] = item.click_count
            dwell_times[item.category_name] = item.dwell_time

    locked_categories = [it.category_name for it in bpy.context.scene.npt_categories if it.is_locked]

    ordered_changes = {}
    for cat_name in sorted(category_order.keys(), key=lambda x: category_order[x]):
        for cls in bpy.types.Panel.__subclasses__():
            if (hasattr(cls, 'bl_space_type') and
                hasattr(cls, 'bl_region_type') and
                hasattr(cls, 'bl_category') and
                cls.bl_space_type == 'VIEW_3D' and
                cls.bl_region_type == 'UI' and
                getattr(cls, 'bl_category', 'Tool').strip() == cat_name):
                panel_name = cls.__name__
                ordered_changes[panel_name] = cat_name

    data_to_save = {
        "changes": ordered_changes,
        "category_order": category_order,
        "click_counts": click_counts,
        "dwell_times": dwell_times,
        "locked_categories": locked_categories,
    }
    save_py_data(data_to_save)

def apply_saved_changes():
    saved_data = load_py_data()
    if not saved_data:
        return
    changes = saved_data.get("changes", {})
    if not changes:
        return
    for panel_name, new_category in changes.items():
        for cls in bpy.types.Panel.__subclasses__():
            if cls.__name__ == panel_name:
                if panel_name not in original_data:
                    original_data[panel_name] = {
                        'category': cls.bl_category,
                        'module': cls.__module__
                    }
                try:
                    bpy.utils.unregister_class(cls)
                    cls.bl_category = new_category
                    bpy.utils.register_class(cls)
                except Exception:
                    try:
                        cls.bl_category = original_data[panel_name]['category']
                        bpy.utils.register_class(cls)
                    except:
                        pass
                break


# ── startup ──
@persistent
def load_handler(dummy):
    n_sua()

def n_sua():
    try:
        apply_saved_changes()
    except Exception:
        pass
    try:
        bpy.ops.npt.refresh_ui()
    except Exception:
        pass


classes = (
    NPT_CategoryItem,
    NPT_OT_refresh_ui,
    NPT_OT_apply_tag,
    NPT_OT_re_tag,
    NPT_OT_move_category,
    NPT_OT_toggle_lock,
    NPT_OT_toggle_auto_sort,
    NPT_PT_panel_ui,
)

def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.Scene.npt_categories = bpy.props.CollectionProperty(type=NPT_CategoryItem)
    bpy.types.Scene.npt_auto_sort = bpy.props.BoolProperty(
        name="自动排序",
        description="根据停留时长自动排序面板标签（系统标签和锁定标签除外）",
        default=False
    )
    bpy.app.handlers.load_post.append(load_handler)
    bpy.app.timers.register(n_sua, first_interval=0.5)
    start_dwell_timer()

def unregister():
    stop_dwell_timer()
    remove_draw_trackers()
    bpy.app.handlers.load_post.remove(load_handler)
    if hasattr(bpy.types.Scene, "npt_auto_sort"):
        del bpy.types.Scene.npt_auto_sort
    if hasattr(bpy.types.Scene, "npt_categories"):
        del bpy.types.Scene.npt_categories
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)

if __name__ == "__main__":
    register()
