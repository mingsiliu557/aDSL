"""Hand-written four-part/four-interface fixture; dimensions and clearance in mm.

Connection order evaluates poses and consistency, not a physical insertion path.
"""
from adsl.core import *
import numpy as np

assembly = FixedAssembly(root_id='left_side', mm_per_unit=1)
assembly.add_part('left_side', Cube((12,60,100), center=(0,0,50)), components=('left_side',))
assembly.add_part('right_side', Cube((12,60,100), center=(0,0,50)), components=('right_side',))
assembly.add_part('upper_shelf', Cube((108,50,8)), components=('upper_shelf',))
assembly.add_part('lower_shelf', Cube((108,50,8)), components=('lower_shelf',))
shelf_joint = TabSlot(width_mm=16, thickness_mm=4, insertion_mm=4,
    slot_depth_mm=5, fit_offset_mm=0.2, root_overlap_mm=0.5,
    opening_extension_mm=0.5, lead_in_mm=0)

assembly.connect('upper_left', tab_part='upper_shelf', tab_port='left_tab',
    slot_part='left_side', slot_port='upper_slot', parameters=shelf_joint, parameter_name='shelf_joint',
    tab_frame=InterfaceFrame((-54,0,0), (0,1,0), (-1,0,0)),
    slot_frame=InterfaceFrame((6,0,70), (0,1,0), (-1,0,0)))
# The placed tab end locates the new right panel's slot end.
assembly.connect('upper_right', tab_part='upper_shelf', tab_port='right_tab',
    slot_part='right_side', slot_port='upper_slot', parameters=shelf_joint, parameter_name='shelf_joint',
    tab_frame=InterfaceFrame((54,0,0), (0,1,0), (1,0,0)),
    slot_frame=InterfaceFrame((-6,0,70), (0,1,0), (1,0,0)))
assembly.connect('lower_left', tab_part='lower_shelf', tab_port='left_tab',
    slot_part='left_side', slot_port='lower_slot', parameters=shelf_joint, parameter_name='shelf_joint',
    tab_frame=InterfaceFrame((-54,0,0), (0,1,0), (-1,0,0)),
    slot_frame=InterfaceFrame((6,0,30), (0,1,0), (-1,0,0)))

before_closure = {name:matrix.copy() for name,matrix in assembly.transforms.items()}
assembly.connect('lower_right', tab_part='lower_shelf', tab_port='right_tab',
    slot_part='right_side', slot_port='lower_slot', parameters=shelf_joint, parameter_name='shelf_joint',
    tab_frame=InterfaceFrame((54,0,0), (0,1,0), (1,0,0)),
    slot_frame=InterfaceFrame((-6,0,30), (0,1,0), (1,0,0)))
for name, matrix in before_closure.items():
    assert np.array_equal(assembly.transforms[name], matrix), name
scene = assembly.scene()
