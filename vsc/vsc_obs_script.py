import json
import os

import obspython as obs
import zmq


class OBS_Socket():
    def __init__(self, socket_type=zmq.REP, poller_type=zmq.POLLIN):
        self._port = 0
        self.socket = zmq.Context().socket(socket_type)
        self.poller = zmq.Poller()
        self.poller.register(self.socket, poller_type)

    def bind(self, port):
        if not self._port:
            self.socket.bind(f"tcp://127.0.0.1:{port}")
            self._port = port

        elif self._port != port:
            self.socket.unbind(f"tcp://127.0.0.1:{self._port}")
            self.socket.bind(f"tcp://127.0.0.1:{port}")
            self._port = port

    def poll(self, rcv_time=0):
        return self.poller.poll(rcv_time)

    def recv(self):
        return self.socket.recv_pyobj()

    def send(self, msg=b""):
        return self.socket.send_pyobj(msg)


class OBS_ScriptSettings():

    getter_fun = {bool: obs.obs_data_get_bool,
                  str: obs.obs_data_get_string,
                  int: obs.obs_data_get_int,
                  float: obs.obs_data_get_double}
    default_fun = {bool: obs.obs_data_set_default_bool,
                   str: obs.obs_data_set_default_string,
                   int: obs.obs_data_set_default_int,
                   float: obs.obs_data_set_default_double}

    def __init__(self, settings):
        self.prop_obj = None
        self.settings = settings

    def __getattr__(self, arg):
        return self.settings[arg]

    def create_properies(self):
        self.prop_obj = obs.obs_properties_create()

    def add_path(self, name, description):
        obs.obs_properties_add_path(self.prop_obj, name, description,
                                    obs.OBS_PATH_DIRECTORY, "",
                                    os.path.expanduser("~"))

    def add_int(self, name, description, vals):
        obs.obs_properties_add_int(self.prop_obj, name,
                                   description, *vals)

    def add_float_slider(self, name, description, vals):
        obs.obs_properties_add_float_slider(self.prop_obj, name,
                                            description, *vals)

    def add_list(self, name, description, source_type):
        p = obs.obs_properties_add_list(self.prop_obj, name,
                                        description,
                                        obs.OBS_COMBO_TYPE_EDITABLE,
                                        obs.OBS_COMBO_FORMAT_STRING)

        def _add_sources(source_type, prop):
            sources = obs.obs_enum_sources()
            if sources is not None:
                for source in sources:
                    source_id = obs.obs_source_get_id(source)
                    if source_id == source_type:
                        name = obs.obs_source_get_name(source)
                        obs.obs_property_list_add_string(prop, name, name)
            obs.source_list_release(sources)

        _add_sources(source_type, p)

    def add_button(self, name, description, callback):
        obs.obs_properties_add_button(self.prop_obj, name,
                                      description, callback)

    def set_defaults(self, settings):
        for name, value in self.settings.items():
            self.default_fun[type(value)](settings, name, value)

    def update(self, settings):
        for name, value in self.settings.items():
                self.settings[name] = self.getter_fun[type(value)](settings, name)



class OBS_Sceneitem():
    def __init__(self, source_name):
        self.source_name = source_name
        self.sceneitem = self._set_sceneitem(self.source_name)

    def _set_sceneitem(self, source_name):
        source = obs.obs_frontend_get_current_scene()
        scene = obs.obs_scene_from_source(source)
        sceneitem = obs.obs_scene_find_source(scene, source_name)
        obs.obs_source_release(source)
        return sceneitem

    def _scale(self):
        ratio = obs.vec2()
        obs.obs_sceneitem_get_scale(self.sceneitem, ratio)
        return ratio

    def _crop(self):
        crop = obs.obs_sceneitem_crop()
        obs.obs_sceneitem_get_crop(self.sceneitem, crop)
        return crop

    def source_size(self):
        source = obs.obs_get_source_by_name(self.source_name)
        w = obs.obs_source_get_width(source)
        h = obs.obs_source_get_height(source)
        obs.obs_source_release(source)
        return w, h

    def monitor_info(self):
        w, h = self.source_size()
        crop = self._crop()
        scale = self._scale()

        info = {"source_size": (w, h),
                "crop": (crop.top, crop.left, crop.right, crop.bottom),
                }

        return info

DEFAULTS = {"pred_threshold": 0.5,
            "monitor": 1,
            "port": 5557,
            "interval": 30,
            "source": ""
}
socket = OBS_Socket()
stgs = OBS_ScriptSettings(settings=DEFAULTS)


def script_description():
    return "Blurs the recording if inappropriate imagery detected"


def script_properties():
    stgs.create_properies()
    stgs.add_float_slider("pred_threshold", "Prediction Threshold", (0.0, 1.0, 0.05))
    stgs.add_int("monitor", "Monitor Number", (1, 100, 1))
    stgs.add_int("port", "Port", (1, 10000, 1))
    stgs.add_int("interval", "Quiery interval(ms)", (1, 10000, 1))
    stgs.add_list("source", "Blur Source", source_type="monitor_capture")
    stgs.add_button("disable", "Disable Source", disable_button)
    return stgs.prop_obj


def script_defaults(settings):
    stgs.set_defaults(settings)


def disable_button(properties, button):
    source = obs.obs_get_source_by_name(stgs.source)
    enabled = obs.obs_source_enabled(source)
    if enabled:
        obs.obs_source_set_enabled(source, False)
    obs.obs_source_release(source)


def blur(pred):
    # Switches blur on if probability is high
    source = obs.obs_get_source_by_name(stgs.source)
    blured = obs.obs_source_enabled(source)
    if blured and pred <= stgs.pred_threshold:
        obs.obs_source_set_enabled(source, False)
    if not blured and pred > stgs.pred_threshold:
        obs.obs_source_set_enabled(source, True)
    obs.obs_source_release(source)


def monitor_info():
    conf = OBS_Sceneitem(source_name=stgs.source).monitor_info()
    conf['monitor_num'] = stgs.monitor
    return conf


def update_status():
    if socket.poll():
        msg = socket.recv()
        if msg['msg'] == 'screen':
            socket.send(monitor_info())
        elif msg['msg'] == 'predict':
            socket.send()
            blur(msg['pred'])


def script_update(settings):
    stgs.update(settings)
    socket.bind(stgs.port)
    obs.timer_add(update_status, stgs.interval)
