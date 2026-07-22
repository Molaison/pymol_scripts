"""Viewport-fixed coordinate axes with group-follow ownership metadata."""

from pymol import cmd
from chempy import cpv
import os
import sys
import threading
import traceback


def _follow_registration_functions():
    try:
        from pymolplugins.follow.commands import (
            register_callback_owned_object,
            unregister_callback_owned_object,
        )
    except ImportError:  # ``run /absolute/path/axes_overlay.py`` from PyMOL.
        root = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
        if root not in sys.path:
            sys.path.insert(0, root)
        from pymolplugins.follow.commands import (  # type: ignore
            register_callback_owned_object,
            unregister_callback_owned_object,
        )
    return register_callback_owned_object, unregister_callback_owned_object


def _register_callback_owned(name):
    register, _ = _follow_registration_functions()
    register(name)


def _unregister_callback_owned(name):
    _, unregister = _follow_registration_functions()
    unregister(name)


class PutCenterCallback:
    prev_v = None

    def __init__(self, name, corner=0):
        self.name = name
        self.corner = corner
        self.cb_name = cmd.get_unused_name("_cb")

    def load(self):
        cmd.load_callback(self, self.cb_name)

    def __call__(self):
        if self.name not in cmd.get_names("objects"):
            try:
                _unregister_callback_owned(self.name)
            except Exception as exc:
                print(f"Error: failed to unregister axes overlay '{self.name}': {exc}")
                traceback.print_exc()
            threading.Thread(
                target=cmd.delete,
                args=(self.cb_name,),
                daemon=True,
            ).start()
            return

        view = cmd.get_view()
        if view == self.prev_v:
            return
        self.prev_v = view
        translation = view[12:15]

        if self.corner:
            viewport = cmd.get_viewport()
            rotation = [view[0:3], view[3:6], view[6:9]]
            camera_offset = [
                0.15 * view[11] * viewport[0] / viewport[1],
                0.15 * view[11],
                0.0,
            ]
            if self.corner in [2, 3]:
                camera_offset[0] *= -1
            if self.corner in [3, 4]:
                camera_offset[1] *= -1
            translation = cpv.add(
                translation,
                cpv.transform(rotation, camera_offset),
            )

        scale = -view[11] / 30.0
        matrix = [
            scale, 0, 0, 0,
            0, scale, 0, 0,
            0, 0, scale, 0,
            translation[0] / scale,
            translation[1] / scale,
            translation[2] / scale,
            1,
        ]
        cmd.set_object_ttt(self.name, matrix)


def axes(name="axes"):
    """Put callback-controlled coordinate axes in the lower-left viewport."""

    from pymol import cgo

    cmd.set("auto_zoom", 0)
    width = 0.06
    length = 0.75
    cone_height = 0.25
    cone_diameter = width * 1.618
    obj = [
        cgo.CYLINDER,
        0.0, 0.0, 0.0, length, 0.0, 0.0, width,
        1.0, 0.0, 0.0, 1.0, 0.0, 0.0,
        cgo.CYLINDER,
        0.0, 0.0, 0.0, 0.0, length, 0.0, width,
        0.0, 1.0, 0.0, 0.0, 1.0, 0.0,
        cgo.CYLINDER,
        0.0, 0.0, 0.0, 0.0, 0.0, length, width,
        0.0, 0.0, 1.0, 0.0, 0.0, 1.0,
        cgo.CONE,
        length, 0.0, 0.0, cone_height + length, 0.0, 0.0,
        cone_diameter, 0.0,
        1.0, 0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0,
        cgo.CONE,
        0.0, length, 0.0, 0.0, cone_height + length, 0.0,
        cone_diameter, 0.0,
        0.0, 1.0, 0.0, 0.0, 1.0, 0.0, 1.0, 1.0,
        cgo.CONE,
        0.0, 0.0, length, 0.0, 0.0, cone_height + length,
        cone_diameter, 0.0,
        0.0, 0.0, 1.0, 0.0, 0.0, 1.0, 1.0, 1.0,
    ]

    cmd.load_cgo(obj, name)
    PutCenterCallback(name, 1).load()
    _register_callback_owned(name)


cmd.extend("axes", axes)
