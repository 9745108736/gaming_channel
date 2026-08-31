"""
Frame layout maths for the 9:16 output.

Two modes, chosen per series with a "layout" key.

  blur_band                       facecam_top
  +----------------+  0           +----------------+  0
  |  text zone     |              |  REACTION CAM  |  full width strip
  +----------------+  band_y      +----------------+  cam_h
  |   GAMEPLAY     |              |  text zone     |  over the gameplay
  +----------------+              +- - - - - - - - +
  |  reaction cam  |              |                |
  +----------------+  safe line   |   GAMEPLAY     |  full bleed
  |  app UI strip  |              |                |
  +----------------+  HEIGHT      +----------------+  HEIGHT

Shorts and Reels overlay their own interface on the bottom SAFE_BOTTOM
pixels, so blur_band centres the band in the USABLE height, not the full
frame. Centring on the full 1920 once left 63% of the reaction cam behind
YouTube's caption and buttons - correct in a desktop player, invisible on
a phone. facecam_top lets the gameplay run full bleed to the bottom
because gameplay is background there; nothing that carries meaning sits
in the covered strip.

process.py draws the gameplay and the reaction cam, export.py draws the
hook and captions. Both read plan() from here. If either computes its own
geometry they drift, and the text lands on the gameplay.
"""

import config


def band_height(fraction=None):
    """
    Pixel height of the gameplay band in blur_band mode.

    Rounded down to even: x264 and nvenc both reject odd dimensions with
    yuv420p, and the failure message points at the filter chain rather
    than at the number that caused it.
    """
    frac = config.GAMEPLAY_HEIGHT if fraction is None else fraction
    return int(config.HEIGHT * frac) // 2 * 2


def usable_height():
    """Frame height minus the strip the platform covers with its own UI."""
    return config.HEIGHT - config.SAFE_BOTTOM


def plan(mode=None, gameplay_height=None, facecam_height=None):
    """
    Where every element goes, for one layout mode.

    Returns a dict:
      mode        the layout name
      band        (y, h) of the gameplay
      text        (y, h) of the zone hook text and captions centre in
      cam_zone    (y, h) of the space the reaction cam occupies
      cam_style   "strip" - full width, always on
                  "bubble" - centred, bordered, sized by REACTION_HEIGHT
    """
    mode = mode or config.DEFAULT_LAYOUT

    if mode == config.LAYOUT_FACECAM_TOP:
        frac = config.FACECAM_HEIGHT if facecam_height is None else facecam_height
        cam_h = int(config.HEIGHT * frac) // 2 * 2
        return {
            "mode": mode,
            "band": (cam_h, config.HEIGHT - cam_h),
            # Text sits over the top of the gameplay, just under the
            # strip. There is no empty zone in this mode by design.
            "text": (cam_h, config.FACECAM_TEXT_BAND),
            "cam_zone": (0, cam_h),
            "cam_style": "strip",
        }

    if mode != config.LAYOUT_BLUR_BAND:
        raise ValueError(
            f"Unknown layout '{mode}'. Known: "
            f"{config.LAYOUT_BLUR_BAND}, {config.LAYOUT_FACECAM_TOP}"
        )

    band = band_height(gameplay_height)
    # Split the leftover space unevenly. An even split gives the text zone
    # room it does not need and starves the reaction cam, which was why
    # the cam could only ever be a narrow 24%-wide sliver.
    spare = usable_height() - band
    top = int(spare * config.TEXT_ZONE_SHARE)
    bottom_y = top + band
    return {
        "mode": mode,
        "band": (top, band),
        "text": (0, top),
        "cam_zone": (bottom_y, usable_height() - bottom_y),
        "cam_style": "bubble",
    }
