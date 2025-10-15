# 水墨风
# 新建背景surface对象，颜色设为灰色
cmd.create(None,"antibody_activate",zoom=0)

set opaque_background, 1
set bg_rgb,white

set transparency, 0.8
set specular, 0
set ambient, 1
set ray_trace_mode, 1
set ray_trace_gain, 1
set cartoon_highlight_color, gray70
set ray_trace_depth_factor, 1 # 越小，trace越明显
set ray_trace_disco_factor, 0.05
ray 2000