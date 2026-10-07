from adsl.core import *
import math

BASE_RADIUS = 13.346818648883376
FRONT_Y = -37.222456715591314
DARK = (0.23, 0.235, 0.22)
RIM = (0.43, 0.43, 0.40)

class CircularBase(Asset):
    def __init__(self):
        super().__init__('circular_base')
        section = Polygon([(0,0),(12.85,0),(13.20,0.15),
                           (BASE_RADIUS,0.45),(BASE_RADIUS,2.55),
                           (13.20,2.95),(12.85,3.3),(12.1,3.6),(0,3.6)])
        self.body = self.attach_part('solid_disk', rotate_extrude(section, segments=128, color=DARK))
        band = Polygon([(13.23,0.35),(BASE_RADIUS,0.45),(BASE_RADIUS,1.55),
                        (13.30,1.7),(13.23,1.7)])
        self.band = self.attach_part('perimeter_band', rotate_extrude(band, segments=128, color=RIM))

class LampHead(Asset):
    def __init__(self):
        super().__init__('offset_lamp_head')
        # A continuous rim surrounds the genuinely inset lower light surface.
        section = Polygon([(0,2.0),(6.65,2.0),(7.15,1.85),(7.45,1.5),
                           (7.5,1.1),(7.5,-1.35),(7.3,-1.85),(6.95,-2.0),
                           (6.4,-2.0),(6.4,-0.85),(0,-0.85)])
        self.housing = self.attach_part('rounded_housing_and_recess', rotate_extrude(section, segments=96, color=DARK))
        self.lens = self.attach_part('inset_light_surface', Cylinder(6.36, height=0.22, center=(0,0,-0.86), color=(0.77,0.77,0.70)))

class Upright(Asset):
    def __init__(self):
        super().__init__('upright_pole')
        self.main = self.attach_part('lower_pole', Cylinder(1.0, p0=(0,0,3.6), p1=(0,0,125.8), color=DARK))
        self.extension = self.attach_part('upper_extension', Cylinder(0.78, p0=(0,0,125.2), p1=(0,0,139.5), color=DARK))
        self.collar = self.attach_part('small_transition_collar', Cylinder(1.06, height=0.65, center=(0,0,125.5), color=RIM))

class CurvedArm(Asset):
    def __init__(self, head):
        super().__init__('curved_upper_arm')
        radius = 0.7
        bend_radius = 4.0
        bend_start_z = 145.3
        points = [(0,0,138.8), (0,0,bend_start_z)]
        bend_samples = 24
        for i in range(1,bend_samples+1):
            t = (math.pi/2)*i/bend_samples
            points.append((0,-bend_radius+bend_radius*math.cos(t),bend_start_z+bend_radius*math.sin(t)))
        hc = shape_center(head)
        end_y = hc[1] + 2.2
        points.append((0,end_y,149.3))
        # The small terminal neck is embedded in the back of the shallow head.
        points.append((0,end_y,147.4))
        segments = []
        for i in range(len(points)-1):
            segments.append(Cylinder(radius,p0=points[i],p1=points[i+1],color=DARK))
        for p in points[1:-1]:
            segments.append(Sphere(radius,center=p,color=DARK))
        self.tube = self.attach_part('continuous_rounded_arm', boolean_union(*segments))

class UpperStructure(Asset):
    def __init__(self):
        super().__init__('upper_structure')
        head = rotate_shape(LampHead(), 'x', 18, center=(0,0,0))
        head = translate_shape(head,(0, FRONT_Y-shape_min(head)[1],149.7-shape_max(head)[2]))
        self.upright_pole = Upright()
        self.curved_upper_arm = CurvedArm(head)
        self.offset_lamp_head = head
        # Structural overlaps are real, rather than tangent-only contacts.
        self.connected_body = self.attach_part('connected_upper_components', boolean_union(self.upright_pole,self.curved_upper_arm,self.offset_lamp_head))

base = CircularBase()
upper = UpperStructure()
assembly = FixedAssembly(root_id='base_body', mm_per_unit=1.0)
assembly.add_part('base_body', base, components=('circular_base',))
assembly.add_part('upper_structure', upper, components=('upright_pole','curved_upper_arm','offset_lamp_head'))
upright_base_fit = TabSlot(width_mm=1.2, thickness_mm=1.2, insertion_mm=2.0,
                          slot_depth_mm=2.2, fit_offset_mm=0.2,
                          root_overlap_mm=0.5, opening_extension_mm=0.5,
                          lead_in_mm=0.15)
assembly.connect('upright_to_base', tab_part='upper_structure', slot_part='base_body',
                 tab_frame=InterfaceFrame(origin=(0,0,3.6),x_axis=(1,0,0),insert_axis=(0,0,-1)),
                 slot_frame=InterfaceFrame(origin=(0,0,3.6),x_axis=(1,0,0),insert_axis=(0,0,-1)),
                 parameters=upright_base_fit, parameter_name='upright_base_fit',
                 tab_port='upright_bottom_tab', slot_port='base_center_blind_slot')
scene = assembly.scene()
