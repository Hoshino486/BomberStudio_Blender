"""Extra operator, animation and updater regression cases shared by the version matrix."""


def run(ns):
    names = ('case', 'reset', 'mesh_object', 'image_file', 'bpy', 'C', 'core', 'bridge',
             'materials', 'model_ops', 'rig_ops', 'atlas', 'OUT', 'os', 'Vector')
    globals().update(dict((name, ns[name]) for name in names))

    @case('shape_snapshots_keep_original_and_save_cleanly')
    def _():
        reset()
        obj = mesh_object(keys=True)
        snapshots = model_ops.shape_snapshots(obj, bpy.context)
        assert len(snapshots) == 3
        assert obj.data.shape_keys and all(s.data.shape_keys is None for s in snapshots)
        aa = next(s for s in snapshots if s['BomberStudioShapeSnapshot'] == 'AA')
        assert abs(aa.data.vertices[0].co.z-1) < 1e-5
        bpy.ops.wm.save_as_mainfile(filepath=os.path.join(OUT,'shape-snapshots.blend'), copy=True)

    @case('position_mapping_and_numeric_group_removal')
    def _():
        reset()
        source, target = mesh_object('Source'), mesh_object('Target')
        for obj, names in [(source, ['Head','Hand']), (target, ['5','8'])]:
            for name, vi in zip(names, [0,1]):
                group = obj.vertex_groups.new(name=name)
                group.add([vi], 1, 'REPLACE')
        count = model_ops.map_groups([source,target], source, .0001)
        assert count == 2 and 'Head' in target.vertex_groups and 'Hand' in target.vertex_groups
        target.vertex_groups.new(name='3')
        model_ops.vertex_group_operation(target, 'VG_REMOVE_NONNUMERIC', bpy.context)
        assert [g.name for g in target.vertex_groups] == ['3']
        model_ops.vertex_group_operation(target, 'VG_PREFIX', bpy.context)
        assert target.vertex_groups[0].name == 'Target_3'

    @case('shared_group_transitive_clustering')
    def _():
        reset()
        mesh = bpy.data.meshes.new('Clusters')
        mesh.from_pydata([(j*2+x,y,0) for j in range(3) for x,y in [(0,0),(1,0),(0,1)]],
                         [], [(0,1,2),(3,4,5),(6,7,8)])
        obj = bpy.data.objects.new('Clusters',mesh)
        C.link(obj)
        g0,g1 = obj.vertex_groups.new(name='0'),obj.vertex_groups.new(name='1')
        g0.add([0,1,2,3],1,'REPLACE')
        g1.add([4,5,6,7,8],1,'REPLACE')
        assert len(model_ops.face_clusters(obj,'SPLIT_LOOSE')) == 3
        assert len(model_ops.face_clusters(obj,'SPLIT_SHARED')) == 1

    @case('break_group_boundaries_keep_shape_keys')
    def _():
        reset()
        mesh = bpy.data.meshes.new('Break')
        mesh.from_pydata([(0,0,0),(1,0,0),(1,1,0),(0,1,0)], [], [(0,1,2),(0,2,3)])
        obj = bpy.data.objects.new('Break',mesh)
        C.link(obj)
        C.select_only([obj])
        obj.shape_key_add(name='Basis')
        key=obj.shape_key_add(name='AA')
        key.data[0].co.z=1
        g0,g1=obj.vertex_groups.new(name='0'),obj.vertex_groups.new(name='1')
        g0.add([0,1],1,'REPLACE')
        g1.add([2,3],1,'REPLACE')
        model_ops.split_model(obj,'BREAK_GROUP',bpy.context)
        assert len(obj.data.vertices) == 6
        assert len(obj.data.shape_keys.key_blocks['AA'].data) == 6

    @case('material_combine_avoids_different_mapping')
    def _():
        reset()
        obj = mesh_object(two_faces=True)
        mat = bpy.data.materials.new('Same')
        materials.attach_texture(mat,image_file('MergeTex',(.4,.6,.2,1)),uv='UV0')
        other = mat.copy()
        obj.data.materials.append(mat)
        obj.data.materials.append(other)
        obj.data.polygons[1].material_index=1
        assert materials.combine_materials([obj]) == 1
        assert len(obj.data.materials) == 1
        other = mat.copy()
        mapping=next(n for n in other.node_tree.nodes if n.type=='MAPPING')
        if hasattr(mapping,'translation'):
            mapping.translation.x=.5
        else:
            mapping.inputs['Location'].default_value.x=.5
        obj.data.materials.append(other)
        obj.data.polygons[1].material_index=1
        assert materials.combine_materials([obj]) == 0
        assert len(obj.data.materials) == 2

    @case('armature_merge_attach_and_csv_roundtrip')
    def _():
        cfg=reset()
        first=mesh_object('First')
        g=first.vertex_groups.new(name='Head')
        g.add([0,1,2],1,'REPLACE')
        rig1=rig_ops.generate_bones(bpy.context)
        second=mesh_object('Second')
        g=second.vertex_groups.new(name='Head')
        g.add([0,1,2],1,'REPLACE')
        rig2=rig_ops.generate_bones(bpy.context)
        C.select_only([rig1,rig2])
        result=rig_ops.merge_rigs(bpy.context)
        assert result == rig1 and len(result.data.bones) == 2
        assert first.find_armature() == rig1 and second.find_armature() == rig1
        C.select_only([rig1,first])
        cfg.parent_bone=''
        rig_ops.attach_meshes(bpy.context)
        assert first.parent == rig1
        C.select_only([first])
        records=rig_ops.translate(bpy.context, {'First':'Body'}, False)
        assert first.name=='Body' and records
        cfg.csv_path=os.path.join(OUT,'mapping.csv')
        assert bpy.ops.bomberstudio.rig(action='EXPORT_CSV') == {'FINISHED'}
        assert os.path.isfile(cfg.csv_path)
        assert bpy.ops.bomberstudio.rig(action='IMPORT_CSV') == {'FINISHED'}

    @case('scoped_physics_removal_leaves_unselected_model')
    def _():
        reset()
        parent=mesh_object('ModelRoot')
        child=mesh_object('Physics')
        child.parent=parent
        child['mmd_type']='RIGID_BODY'
        other=mesh_object('UnrelatedPhysics')
        other['mmd_type']='RIGID_BODY'
        C.select_only([parent])
        assert rig_ops.remove_physics(bpy.context)==1
        assert 'Physics' not in bpy.data.objects and other.name in bpy.data.objects

    @case('animation_export_import_world_vertex_samples')
    def _():
        cfg=reset()
        obj=mesh_object(keys=True)
        group=obj.vertex_groups.new(name='Root')
        group.add([0,1,2],1,'REPLACE')
        rig=rig_ops.generate_bones(bpy.context)
        pose=rig.pose.bones['Root']
        pose.rotation_mode='XYZ'
        for frame,angle in [(1,0),(2,.2),(3,0)]:
            pose.rotation_euler.z=angle
            pose.keyframe_insert('rotation_euler',frame=frame)
        key=obj.data.shape_keys.key_blocks['AA']
        for frame,value in [(1,0),(2,1),(3,0)]:
            key.value=value
            key.keyframe_insert('value',frame=frame)
        scene=bpy.context.scene
        scene.frame_start,scene.frame_end=1,3
        def sample(objects,frame):
            scene.frame_set(frame)
            C.update()
            points=[]
            for ob in objects:
                if ob.type!='MESH':continue
                mesh,release=C.evaluated_mesh(ob)
                try:
                    points.extend(tuple(C.matmul(ob.matrix_world,v.co)) for v in mesh.vertices)
                finally:
                    release()
            return points
        expected=[sample([obj],f) for f in (1,2,3)]
        assert expected[0] != expected[1]
        C.select_only([rig,obj])
        cfg.export_animations=True
        cfg.export_all_actions=False
        path=os.path.join(OUT,'three-frame-animation.fbx')
        assert bpy.ops.bomberstudio.export_fbx(filepath=path)=={'FINISHED'}
        reset()
        imported,_=bridge.import_model(path,bpy.context,animations=True,animation_offset=0)
        for frame,wanted in zip((1,2,3),expected):
            actual=sample(imported,frame)
            assert len(actual)==len(wanted)
            error=max((Vector(a)-Vector(b)).length for a,b in zip(actual,wanted))
            assert error<1e-4,(frame,error,actual,wanted)

    @case('update_zip_validate_install_restore_isolated_fixture')
    def _():
        import zipfile
        from bomberstudio_blender import management
        cfg=reset()
        cfg.temp_dir=os.path.join(OUT,'updater')
        os.makedirs(cfg.temp_dir,exist_ok=True)
        fixture=os.path.join(cfg.temp_dir,'fixture-addon')
        os.makedirs(fixture,exist_ok=True)
        init=os.path.join(fixture,'__init__.py')
        core.atomic_bytes(init,b'# BomberStudio old fixture\\nVERSION=0\\n',False)
        core.atomic_bytes(os.path.join(fixture,'management.py'),b'# fixture\\n',False)
        package=os.path.join(cfg.temp_dir,'valid.zip')
        with zipfile.ZipFile(package,'w') as z:
            z.writestr('bomberstudio_blender/__init__.py','# BomberStudio test fixture\nVERSION=1\n')
            z.writestr('bomberstudio_blender/core.py','VERSION=(1,0,0)\n')
        assert '__init__.py' in management.validate_addon_zip(package)
        malicious=os.path.join(cfg.temp_dir,'bad.zip')
        with zipfile.ZipFile(malicious,'w') as z:
            z.writestr('__init__.py','# BomberStudio\n')
            z.writestr('core.py','VERSION=(1,0,0)')
            z.writestr('../escape.py','print(1)')
        try:
            management.validate_addon_zip(malicious)
        except ValueError:
            pass
        else:
            raise AssertionError('ZIP traversal was accepted')
        original=management.__file__
        management.__file__=os.path.join(fixture,'management.py')
        try:
            receipt=management.install_local_zip(package,bpy.context)
            assert os.path.isfile(os.path.join(receipt['backup'],'__init__.py'))
            with open(init,'r') as f:assert 'VERSION=1' in f.read()
            assert bpy.ops.bomberstudio.manage(action='RESTORE_UPDATE')=={'FINISHED'}
            with open(init,'r') as f:assert 'VERSION=0' in f.read()
        finally:
            management.__file__=original

    @case('invalid_selection_reports_cancel_without_scene_edits')
    def _():
        reset()
        before=set(bpy.data.objects)
        try:
            result=bpy.ops.bomberstudio.model(action='VG_REMOVE_ALL')
            assert result=={'CANCELLED'}
        except RuntimeError as exc:
            # Blender raises for report(ERROR) even when operator returns CANCELLED.
            assert '请先选中网格物体' in str(exc)
        assert set(bpy.data.objects)==before
