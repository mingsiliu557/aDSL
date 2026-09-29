from adsl.core import *
class Sample(Asset):
    def __init__(self):
        super().__init__()
        body=linear_extrude(Polygon([(0,0),(4,0),(4,1),(1,1),(1,3),(0,3)]),2)
        body=boolean_difference(body,Cylinder(.2,p0=(.5,.5,-1),p1=(.5,.5,3)))
        self.attach_part("profile",body)
        ring=rotate_extrude(Polygon([(1,0),(2,0),(2,1),(1,1)]),angle=180)
        self.attach_part("ring",align_anchors(ring,body,anchor="left",target_anchor="right",offset=(1,0,0)))
        self.attach_part("transition",translate_shape(hull(Cube((1,1,1)),translate_shape(Sphere(.5),(2,0,1))),(0,5,0)))
        self.attach_part("legacy",translate_shape(boolean_difference(Cube((3,3,2)),Cylinder(.5,p0=(0,0,-2),p1=(0,0,2))),(6,5,0)))
scene=Sample()
