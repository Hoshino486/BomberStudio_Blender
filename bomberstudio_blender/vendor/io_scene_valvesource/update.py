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


# Modified by BomberStudio integration, 2026-10-03.
# Never overwrite the nested provider using the standalone updater.
import bpy

class SMD_MT_Updated(bpy.types.Menu):
    bl_label = "BomberStudio Source Tools"
    def draw(self, context):
        self.layout.label(text="Source Tools 随 BomberStudio 统一安装包更新")

class SmdToolsUpdate(bpy.types.Operator):
    bl_idname = "script.update_smd"
    bl_label = "检查 BomberStudio 集成包更新"
    bl_description = "由 BomberStudio 统一管理内置 Source Tools；只检查，不自动安装"

    def execute(self, context):
        return bpy.ops.bomberstudio.manage(action='CHECK_UPDATE')
