"""Two physical print parts; dimensions in mm. Demonstration clearance, not calibrated fit."""
from adsl.core import *


class Crossbar(Asset):
    def __init__(self):
        super().__init__()
        self.attach_part('body', Cube((60, 20, 12), center=(0, 0, 6)))


class Stem(Asset):
    def __init__(self):
        super().__init__()
        self.attach_part('body', Cube((20, 12, 50), center=(0, 0, -25)))


joint = TabSlot(width_mm=12, thickness_mm=6, insertion_mm=6,
                slot_depth_mm=7, fit_offset_mm=0.2, lead_in_mm=0.5)
assembly = FixedAssembly(root_id='crossbar', mm_per_unit=1)
assembly.add_part('crossbar', Crossbar(), components=('crossbar',))
assembly.add_part('stem', Stem(), components=('stem',))
assembly.connect('stem_to_crossbar', tab_part='stem', slot_part='crossbar',
    tab_frame=InterfaceFrame(), slot_frame=InterfaceFrame(), parameters=joint,
    parameter_name='joint', tab_port='tab', slot_port='slot')
scene = assembly.scene()
