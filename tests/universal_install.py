"""Executed inside Blender; no system profiles or preferences are saved."""
import addon_utils
import ast
import bpy
import hashlib
import importlib
import json
import os
import sys
import zipfile

archive, mode, out = sys.argv[sys.argv.index('--') + 1:]
out = os.path.realpath(out)
os.makedirs(out, exist_ok=True)
with zipfile.ZipFile(archive) as package:
    source = package.read('bomberstudio_blender/__init__.py').decode('utf8')
    metadata = next(ast.literal_eval(node.value) for node in ast.parse(source).body
                    if isinstance(node, ast.Assign) and getattr(node.targets[0], 'id', None) == 'bl_info')
    assert metadata['blender'] == (2, 79, 0)
    assert 'bomberstudio_blender/blender_manifest.toml' in package.namelist()
    expected_version = tuple(metadata['version'])

if bpy.app.version < (2, 80, 0):
    scripts = os.path.join(out, 'scripts')
    bpy.context.user_preferences.filepaths.script_directory = scripts
else:
    scripts = bpy.utils.user_resource('SCRIPTS')
assert os.path.commonpath([out, os.path.realpath(scripts)]) == out

if mode == 'Legacy':
    if bpy.app.version < (2, 80, 0):
        result = bpy.ops.wm.addon_install(filepath=archive, overwrite=True, target='PREFS')
    else:
        result = bpy.ops.preferences.addon_install(filepath=archive, overwrite=True)
    assert result == {'FINISHED'}
    bpy.utils.refresh_script_paths()
    addon_utils.modules_refresh()
    name = 'bomberstudio_blender'
    assert addon_utils.enable(name, default_set=False, persistent=False) is not None
else:
    directory = os.path.join(out, 'extension-repo')
    os.makedirs(directory, exist_ok=True)
    repo = bpy.context.preferences.extensions.repos.new(
        name='BomberStudio Universal Isolated Test', module='bomber_universal', custom_directory=directory)
    repo.use_custom_directory = True
    repo.use_remote_url = False
    repo.enabled = True
    assert os.path.commonpath([out, os.path.realpath(repo.directory)]) == out
    assert bpy.ops.extensions.package_install_files(
        filepath=archive, repo=repo.module, enable_on_install=True) == {'FINISHED'}
    name = 'bl_ext.' + repo.module + '.bomberstudio_blender'

addon = importlib.import_module(name)
C = importlib.import_module(name + '.compat')
core = importlib.import_module(name + '.core')
management = importlib.import_module(name + '.management')
assert os.path.commonpath([out, os.path.realpath(addon.__file__)]) == out, addon.__file__
assert core.VERSION == expected_version
assert hasattr(bpy.types.Scene, 'bomberstudio')
if mode == 'Legacy':
    assert addon.bl_info['blender'] == ((2, 79, 0) if bpy.app.version < (2, 80, 0) else (2, 80, 0))
cfg = bpy.context.scene.bomberstudio
cfg.temp_dir = out
cfg.output_dir = os.path.join(out, 'output')
os.makedirs(cfg.output_dir, exist_ok=True)

# Exercise the public-install fallback without creating files there.
local = C._LOCAL_WORKSPACE
C._LOCAL_WORKSPACE = os.path.join(out, 'nonexistent-workspace')
try:
    expected = os.path.join(bpy.utils.user_resource('CONFIG') or os.path.expanduser('~'), 'bomberstudio')
    assert C.default_workspace() == expected
finally:
    C._LOCAL_WORKSPACE = local

bpy.ops.mesh.primitive_cube_add()
assert bpy.ops.bomberstudio.model(action='NORMAL_UV') == {'FINISHED'}
assert 'Bomber_SmoothNormal_Oct' in bpy.context.object.data.uv_layers

# Portable synthetic mesh: test the complete installed ASCII-to-native import.
fixture = os.path.join(out, 'triangle_ascii.fbx')
with open(fixture, 'w', encoding='utf8') as stream:
    stream.write('''; FBX 7.4.0 project file
FBXHeaderExtension: { FBXHeaderVersion: 1003
FBXVersion: 7400
}
GlobalSettings: {
  Version: 1000
  Properties70: {
    P: "UnitScaleFactor", "double", "Number", "", 100
  }
}
Objects: {
  Geometry: 100, "Geometry::Triangle", "Mesh" {
    Vertices: *9 { a: 0,0,0,1,0,0,0,1,0 }
    PolygonVertexIndex: *3 { a: 0,1,-3 }
  }
  Model: 200, "Model::Triangle", "Mesh" {
    Version: 232
    Properties70: {
      P: "Lcl Translation", "Lcl Translation", "", "A", 0,0,0
      P: "Lcl Rotation", "Lcl Rotation", "", "A", 0,0,0
      P: "Lcl Scaling", "Lcl Scaling", "", "A", 1,1,1
    }
  }
}
Connections: {
  C: "OO", 100,200
  C: "OO", 200,0
}
''')
before = set(bpy.data.objects)
assert bpy.ops.bomberstudio.import_model(filepath=fixture) == {'FINISHED'}
created = [o for o in bpy.data.objects if o not in before and o.type == 'MESH']
assert len(created) == 1 and len(created[0].data.vertices) == 3
assert len(created[0].data.polygons) == 1
with open(os.path.join(out, 'last-import.json'), encoding='utf8') as stream:
    imported = json.load(stream)
assert imported['fbx_conversion']['converted']
assert os.path.realpath(imported['model']) == os.path.realpath(fixture)

# The same ZIP must work with the built-in update/rollback path.
content = management.validate_addon_zip(archive)
assert '__init__.py' in content and 'blender_manifest.toml' in content
receipt = management.install_local_zip(archive, bpy.context)
assert os.path.commonpath([out, receipt['target']]) == out
assert os.path.isdir(receipt['backup']) and os.path.isdir(receipt['previous'])
assert bpy.ops.bomberstudio.manage(action='RESTORE_UPDATE') == {'FINISHED'}

addon_utils.disable(name, default_set=False)
assert not hasattr(bpy.types.Scene, 'bomberstudio')
assert addon_utils.enable(name, default_set=False, persistent=False) is not None
assert hasattr(bpy.types.Scene, 'bomberstudio')
addon_utils.disable(name, default_set=False)
assert not hasattr(bpy.types.Scene, 'bomberstudio')

with open(archive, 'rb') as stream:
    digest = hashlib.sha256(stream.read()).hexdigest()
result = {
    'passed': True, 'blender': bpy.app.version_string, 'mode': mode,
    'addon_version': list(expected_version), 'module': name, 'module_path': addon.__file__,
    'archive_sha256': digest, 'isolated': True, 'preferences_saved': False,
    'ascii_triangle_import': True, 'normal_uv': True, 'portable_default_path': True,
    'local_update_and_rollback': True, 'disable_reenable': True,
}
with open(os.path.join(out, 'installed.json'), 'w', encoding='utf8') as stream:
    json.dump(result, stream, ensure_ascii=False, indent=2)
print('UNIVERSAL_INSTALL_PASS', json.dumps(result, ensure_ascii=False), flush=True)
