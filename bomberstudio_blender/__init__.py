# SPDX-License-Identifier: GPL-3.0-or-later
# Python 3.5 syntax is intentional: Blender 2.79 through modern versions.
bl_info = {
    'name': 'BomberStudio Blender Bridge',
    'author': 'BomberAi',
    'version': (1, 2, 0),
    'blender': (2, 79, 0),
    'location': 'View3D > Sidebar > BomberStudio (2.79: Tool Shelf)',
    'description': 'BomberStudio 导入、模型整理、骨架口型眼球、贴图预览与 PBR 图集',
    'category': 'Import-Export',
}

import bpy
# Static metadata lets 2.79 discover the package. Modern Blender checks the
# imported module after execution; declare its already-adapted 2.80+ API there.
# The same directory also carries a native 4.2+ extension manifest.
if bpy.app.version >= (2, 80, 0):
    bl_info['blender'] = (2, 80, 0)
from . import compat, properties, bridge, model_ops, rig_ops, materials, atlas, online_update, management, source_tools, ui

MODULES = (properties, bridge, model_ops, rig_ops, materials, atlas, management, online_update, source_tools, ui)
_registered = []
_menu = None


def register():
    global _menu
    if _registered:
        return
    try:
        for module in MODULES:
            for cls in module.CLASSES:
                compat.register(cls)
                _registered.append(cls)
        bpy.types.Scene.bomberstudio = bpy.props.PointerProperty(type=properties.BOMBER_PG_settings)
        _menu = getattr(bpy.types, 'TOPBAR_MT_file_import', None) or getattr(bpy.types, 'INFO_MT_file_import', None)
        if _menu:
            _menu.append(ui.menu_import)
        source_tools.register_backend()
    except Exception:
        unregister()
        raise


def unregister():
    global _menu
    online_update.shutdown()
    source_tools.unregister_backend()
    if _menu:
        try:
            _menu.remove(ui.menu_import)
        except (ValueError, RuntimeError):
            pass
        _menu = None
    if hasattr(bpy.types.Scene, 'bomberstudio'):
        del bpy.types.Scene.bomberstudio
    for cls in reversed(_registered):
        bpy.utils.unregister_class(cls)
    _registered[:] = []


if __name__ == '__main__':
    register()
