#  Copyright (c) 2014 Tom Edwards contact@steamreview.org
#
# ##### BEGIN GPL LICENSE BLOCK #####
#
#  This program is free software; you can redistribute it and/or
#  modify it under the terms of the GNU General Public License
#  as published by the Free Software Foundation; either version 2
#  of the License, or (at your option) any later version.
#
#  This program is distributed in the hope that it will be useful,
#  but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
#  GNU General Public License for more details.
#
#  You should have received a copy of the GNU General Public License
#  along with this program; if not, write to the Free Software Foundation,
#  Inc., 51 Franklin Street, Fifth Floor, Boston, MA 02110-1301, USA.
#
# ##### END GPL LICENSE BLOCK #####

bl_info = {
	"name": "Blender Source Tools",
	"author": "Tom Edwards",
	"version": (3, 4, 3),
	"blender": (4, 1, 0),
	"category": "Import-Export",
	"location": "File > Import/Export, Scene properties",
	"wiki_url": "http://steamcommunity.com/groups/BlenderSourceTools",
	"tracker_url": "http://steamcommunity.com/groups/BlenderSourceTools/discussions/0/",
	"description": "Importer and exporter for Valve Software's Source Engine. Supports SMD\\VTA, DMX and QC."
}

import bpy, os
from bpy.props import StringProperty, BoolProperty, EnumProperty, IntProperty, CollectionProperty, FloatProperty, PointerProperty

# BomberStudio integration: lifecycle is owned by the parent add-on.
from . import datamodel, import_smd, export_smd, flex, GUI, update
from .utils import *

class ValveSource_Exportable(bpy.types.PropertyGroup):
	ob_type : StringProperty()
	icon : StringProperty()
	obj : PointerProperty(type=bpy.types.Object)
	collection : PointerProperty(type=bpy.types.Collection)

	@property
	def item(self) -> bpy.types.Object | bpy.types.Collection: return self.obj or self.collection

	@property
	def session_uid(self): return self.item.session_uid

def menu_func_import(self, context):
	self.layout.operator(import_smd.SmdImporter.bl_idname, text=get_id("import_menuitem", True))

def menu_func_export(self, context):
	self.layout.menu("SMD_MT_ExportChoice", text=get_id("export_menuitem"))

def menu_func_shapekeys(self,context):
	self.layout.operator(flex.ActiveDependencyShapes.bl_idname, text=get_id("activate_dependency_shapes",True), icon='SHAPEKEY_DATA')

def menu_func_textedit(self,context):
	self.layout.operator(flex.InsertUUID.bl_idname)

def export_active_changed(self, context):
	if not context.scene.vs.export_list_active < len(context.scene.vs.export_list):
		context.scene.vs.export_list_active = len(context.scene.vs.export_list) - 1
		return

	item = get_active_exportable(context).item
	
	if type(item) == bpy.types.Collection and item.vs.mute: return
	for ob in context.scene.objects: ob.select_set(False)
	
	if type(item) == bpy.types.Collection:
		context.view_layer.objects.active = item.objects[0]
		for ob in item.objects: ob.select_set(True)
	else:
		item.select_set(True)
		context.view_layer.objects.active = item
#
# Property Groups
#
from bpy.types import PropertyGroup

encodings = []
for enc in datamodel.list_support()['binary']: encodings.append( (str(enc), f"Binary {enc}", '' ) )
formats = []
for version in set(x for x in [*dmx_versions_source1.values(), *dmx_versions_source2.values()] if x.format != 0):
	formats.append((version.format_enum, version.format_title, ''))
formats.sort(key = lambda f: f[0])

_relativePathOptions = {'PATH_SUPPORTS_BLEND_RELATIVE'} if bpy.app.version >= (4,5,0) else set()

class ValveSource_SceneProps(PropertyGroup):
	export_path : StringProperty(name=get_id("exportroot"),description=get_id("exportroot_tip"), subtype='DIR_PATH', options=_relativePathOptions)
	qc_compile : BoolProperty(name=get_id("qc_compileall"),description=get_id("qc_compileall_tip"),default=False)
	qc_path : StringProperty(name=get_id("qc_path"),description=get_id("qc_path_tip"),default="//*.qc",subtype="FILE_PATH", options=_relativePathOptions)
	engine_path : StringProperty(name=get_id("engine_path"),description=get_id("engine_path_tip"), subtype='DIR_PATH',update=State.onEnginePathChanged)
	
	dmx_encoding : EnumProperty(name=get_id("dmx_encoding"),description=get_id("dmx_encoding_tip"),items=tuple(encodings),default='2')
	dmx_format : EnumProperty(name=get_id("dmx_format"),description=get_id("dmx_format_tip"),items=tuple(formats),default='1')
	
	export_format : EnumProperty(name=get_id("export_format"),items=( ('SMD', "SMD", "Studiomdl Data" ), ('DMX', "DMX", "Datamodel Exchange" ) ),default='DMX')
	up_axis : EnumProperty(name=get_id("up_axis"),items=axes,default='Z',description=get_id("up_axis_tip"))
	material_path : StringProperty(name=get_id("dmx_mat_path"),description=get_id("dmx_mat_path_tip"))
	export_list_active : IntProperty(name=get_id("active_exportable"),default=0,min=0,update=export_active_changed)
	export_list : CollectionProperty(type=ValveSource_Exportable,options={'SKIP_SAVE','HIDDEN'})
	use_kv2 : BoolProperty(name="Write KeyValues2",description="Write ASCII DMX files",default=False)
	game_path : StringProperty(name=get_id("game_path"),description=get_id("game_path_tip"),subtype='DIR_PATH',update=State.onGamePathChanged)
	dmx_weightlink_threshold : FloatProperty(name=get_id("dmx_weightlinkcull"),description=get_id("dmx_weightlinkcull_tip"),max=1,min=0)
	smd_format : EnumProperty(name=get_id("smd_format"), items=(('SOURCE', "Source", "Source Engine (Half-Life 2)") , ("GOLDSOURCE", "GoldSrc", "GoldSrc engine (Half-Life 1)")), default="SOURCE")

class ValveSource_VertexAnimation(PropertyGroup):
	name : StringProperty(name="Name",default="VertexAnim")
	start : IntProperty(name="Start",description=get_id("vca_start_tip"),default=0)
	end : IntProperty(name="End",description=get_id("vca_end_tip"),default=250)
	export_sequence : BoolProperty(name=get_id("vca_sequence"),description=get_id("vca_sequence_tip"),default=True)

class ExportableProps():
	flex_controller_modes = (
		('SIMPLE',"Simple",get_id("controllers_simple_tip")),
		('ADVANCED',"Advanced",get_id("controllers_advanced_tip"))
	)

	export : BoolProperty(name=get_id("scene_export"),description=get_id("use_scene_export_tip"),default=True)
	subdir : StringProperty(name=get_id("subdir"),description=get_id("subdir_tip"))
	flex_controller_mode : EnumProperty(name=get_id("controllers_mode"),description=get_id("controllers_mode_tip"),items=flex_controller_modes,default='SIMPLE')
	flex_controller_source : StringProperty(name=get_id("controller_source"),description=get_id("controllers_source_tip"),subtype='FILE_PATH', options=_relativePathOptions)

	vertex_animations : CollectionProperty(name=get_id("vca_group_props"),type=ValveSource_VertexAnimation)
	active_vertex_animation : IntProperty(default=-1)

class ValveSource_ObjectProps(ExportableProps,PropertyGroup):
	action_filter : StringProperty(name=get_id("slot_filter") if State.useActionSlots else get_id("action_filter"),description=get_id("slot_filter_tip") if State.useActionSlots else get_id("action_filter_tip"))
	triangulate : BoolProperty(name=get_id("triangulate"),description=get_id("triangulate_tip"),default=False)

class ValveSource_ArmatureProps(PropertyGroup):
	implicit_zero_bone : BoolProperty(name=get_id("dummy_bone"),default=True,description=get_id("dummy_bone_tip"))
	arm_modes = (
		('CURRENT',get_id("action_slot_current"),get_id("action_slot_selection_current_tip")),
		('FILTERED',get_id("slot_filter"),get_id("slot_filter_tip")),
		('FILTERED_ACTIONS',get_id("action_filter"),get_id("action_selection_filter_tip")),
	) if State.useActionSlots else (
		('CURRENT',get_id("action_selection_current"),get_id("action_selection_current_tip")),
		('FILTERED',get_id("action_filter"),get_id("action_selection_filter_tip"))		
	)
	action_selection : EnumProperty(name=get_id("action_selection_mode"), items=arm_modes,description=get_id("action_selection_mode_tip"),default='CURRENT')
	legacy_rotation : BoolProperty(name=get_id("bone_rot_legacy"),description=get_id("bone_rot_legacy_tip"),default=False)

class ValveSource_CollectionProps(ExportableProps,PropertyGroup):
	mute : BoolProperty(name=get_id("group_suppress"),description=get_id("group_suppress_tip"),default=False)
	selected_item : IntProperty(default=-1, max=-1, min=-1)
	automerge : BoolProperty(name=get_id("group_merge_mech"),description=get_id("group_merge_mech_tip"),default=False)

class ShapeTypeProps():
	flex_stereo_sharpness : FloatProperty(name=get_id("shape_stereo_sharpness"),description=get_id("shape_stereo_sharpness_tip"),default=90,min=0,max=100,subtype='PERCENTAGE')
	flex_stereo_mode : EnumProperty(name=get_id("shape_stereo_mode"),description=get_id("shape_stereo_mode_tip"),
								 items=tuple(list(axes) + [('VGROUP','Vertex Group',get_id("shape_stereo_mode_vgroup"))]), default='X')
	flex_stereo_vg : StringProperty(name=get_id("shape_stereo_vgroup"),description=get_id("shape_stereo_vgroup_tip"))

class CurveTypeProps():
	faces : EnumProperty(name=get_id("curve_poly_side"),description=get_id("curve_poly_side_tip"),default='FORWARD',items=(
	('FORWARD', get_id("curve_poly_side_fwd"), ''),
	('BACKWARD', get_id("curve_poly_side_back"), ''),
	('BOTH', get_id("curve_poly_side_both"), '')) )

class ValveSource_MeshProps(ShapeTypeProps,PropertyGroup):
	pass
class ValveSource_SurfaceProps(ShapeTypeProps,CurveTypeProps,PropertyGroup):
	pass
class ValveSource_CurveProps(ShapeTypeProps,CurveTypeProps,PropertyGroup):
	pass
class ValveSource_TextProps(CurveTypeProps,PropertyGroup):
	pass

_classes = (
	ValveSource_Exportable,
	ValveSource_SceneProps,
	ValveSource_VertexAnimation,
	ValveSource_ObjectProps,
	ValveSource_ArmatureProps,
	ValveSource_CollectionProps,
	ValveSource_MeshProps,
	ValveSource_SurfaceProps,
	ValveSource_CurveProps,
	ValveSource_TextProps,
	GUI.SMD_MT_ExportChoice,
	GUI.SMD_PT_Scene,
	GUI.SMD_MT_ConfigureScene,
	GUI.SMD_UL_ExportItems,
	GUI.SMD_UL_GroupItems,
	GUI.SMD_UL_VertexAnimationItem,
	GUI.SMD_OT_AddVertexAnimation,
	GUI.SMD_OT_RemoveVertexAnimation,
	GUI.SMD_OT_PreviewVertexAnimation,
	GUI.SMD_OT_GenerateVertexAnimationQCSnippet,
	GUI.SMD_OT_LaunchHLMV,
	GUI.SMD_PT_Object_Config,
	GUI.SMD_PT_Group,
	GUI.SMD_PT_VertexAnimation,
	GUI.SMD_PT_Armature,
	GUI.SMD_PT_ShapeKeys,
	GUI.SMD_PT_VertexMaps,
	GUI.SMD_PT_Curves,
	GUI.SMD_PT_Scene_QC_Complie,
	flex.DmxWriteFlexControllers,
	flex.AddCorrectiveShapeDrivers,
	flex.RenameShapesToMatchCorrectiveDrivers,
	flex.ActiveDependencyShapes,
	flex.InsertUUID,
	update.SmdToolsUpdate,
	update.SMD_MT_Updated,
	export_smd.SMD_OT_Compile, 
	export_smd.SmdExporter, 
	import_smd.SmdImporter) + tuple(GUI.VERTEX_MAP_CLASSES)


# Modified by BomberStudio integration, 2026-10-03.
# Only resources successfully registered by this copy may be removed.
_registered_classes = []
_registered_menus = []
_registered_pointers = []
_translations_registered = False

def register():
    global _translations_registered
    if _registered_classes:
        return
    if hasattr(bpy.types.Scene, "vs"):
        raise RuntimeError("Another Source Tools provider already owns Scene.vs")
    try:
        for cls in _classes:
            bpy.utils.register_class(cls)
            _registered_classes.append(cls)
        from . import translations
        bpy.app.translations.register(__name__, translations.translations)
        _translations_registered = True
        for menu, callback in (
            (bpy.types.TOPBAR_MT_file_import, menu_func_import),
            (bpy.types.TOPBAR_MT_file_export, menu_func_export),
            (bpy.types.MESH_MT_shape_key_context_menu, menu_func_shapekeys),
            (bpy.types.TEXT_MT_edit, menu_func_textedit),
        ):
            menu.append(callback)
            _registered_menus.append((menu, callback))
        for owner, prop_type in (
            (bpy.types.Scene, ValveSource_SceneProps),
            (bpy.types.Object, ValveSource_ObjectProps),
            (bpy.types.Armature, ValveSource_ArmatureProps),
            (bpy.types.Collection, ValveSource_CollectionProps),
            (bpy.types.Mesh, ValveSource_MeshProps),
            (bpy.types.SurfaceCurve, ValveSource_SurfaceProps),
            (bpy.types.Curve, ValveSource_CurveProps),
            (bpy.types.Text, ValveSource_TextProps),
        ):
            owner.vs = PointerProperty(name=get_id("settings_prop"), type=prop_type)
            _registered_pointers.append(owner)
        State.hook_events()
    except Exception:
        unregister()
        raise

def unregister():
    global _translations_registered
    State.unhook_events()
    for menu, callback in reversed(_registered_menus):
        menu.remove(callback)
    _registered_menus.clear()
    for owner in reversed(_registered_pointers):
        if hasattr(owner, "vs"):
            delattr(owner, "vs")
    _registered_pointers.clear()
    if _translations_registered:
        bpy.app.translations.unregister(__name__)
        _translations_registered = False
    for cls in reversed(_registered_classes):
        if cls.is_registered:
            bpy.utils.unregister_class(cls)
    _registered_classes.clear()

if __name__ == "__main__":
    register()
