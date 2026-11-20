import numpy as np

BO1_SCALE = 1.6


def adjust_tables(best_of, table, vrs=None):
    if best_of != 1 or BO1_SCALE == 1.0:
        return table
    clipped = np.clip(table, 1e-6, 1 - 1e-6)
    return 1.0 / (1.0 + np.exp(-BO1_SCALE * np.log(clipped / (1.0 - clipped))))
