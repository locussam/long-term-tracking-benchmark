from __future__ import absolute_import
from __future__ import division
from __future__ import print_function

import argparse
import itertools
import json
import numpy as np
import math
import os
import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt

import oxuva

# <REPO_DIR>/scripts/analyze.py
REPO_DIR = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))

FRAME_RATE = 30
MARKERS = ['o', 'v', '^', '<', '>', 's', 'd']  # '*'
CMAP_PREFERENCE = ['tab10', 'tab20', 'hsv']
GRID_COLOR = '0.85'  # plt.rcParams['grid.color']
CLEARANCE = 1.1  # Axis range is CLEARANCE * max_value, rounded up.
ARGS_FORMATTER = argparse.ArgumentDefaultsHelpFormatter  # Show default values
INTERVAL_TYPES = ['before', 'after', 'between']
INTERVAL_AXIS_LABEL = {
    'before': 'Frames before time (min)',
    'after': 'Frames after time (min)',
    'between': 'Frames in interval (min)',
}


def _add_arguments(parser):
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument('--data', default='dev', help='{dev,test,devtest}')
    common.add_argument('--challenge', default='constrained',
                        help='{open,constrained,all}')
    common.add_argument('--verbose', '-v', action='store_true')
    common.add_argument('--permissive', action='store_true',
                        help='Silently exclude tracks which caused an error')
    common.add_argument('--ignore_cache', action='store_true')
    common.add_argument('--cache_dir', default='cache/')
    common.add_argument('--iou_thresholds', nargs='+', type=float, default=[0.5],
                        help='List of IOU thresholds to use', metavar='IOU')

    plot_args = argparse.ArgumentParser(add_help=False)
    plot_args.add_argument('--width_inches', type=float, default=5.0)
    plot_args.add_argument('--height_inches', type=float, default=4.0)

    tpr_tnr_args = argparse.ArgumentParser(add_help=False)
    tpr_tnr_args.add_argument('--no_level_sets', dest='level_sets', action='store_false')
    tpr_tnr_args.add_argument('--no_lower_bounds', dest='lower_bounds', action='store_false')

    subparsers = parser.add_subparsers(dest='subcommand', help='Analysis mode')
    # table: Produce a table (one column per IOU threshold)
    subparser = subparsers.add_parser('table', formatter_class=ARGS_FORMATTER, parents=[common])
    # plot_tpr_tnr: Produce a figure (one figure per IOU threshold)
    subparser = subparsers.add_parser('plot_tpr_tnr', formatter_class=ARGS_FORMATTER,
                                      parents=[common, plot_args, tpr_tnr_args])
    # plot_tpr_tnr_intervals: Produce a figure (one figure per IOU threshold)
    subparser = subparsers.add_parser('plot_tpr_tnr_intervals', formatter_class=ARGS_FORMATTER,
                                      parents=[common, plot_args, tpr_tnr_args])
    subparser.add_argument('--times', type=int, default=[0, 60, 120, 240], help='seconds')
    # plot_tpr_time: Produce a figure for interval ranges (0, t) and (t, inf).
    subparser = subparsers.add_parser('plot_tpr_time', formatter_class=ARGS_FORMATTER,
                                      parents=[common, plot_args])
    subparser.add_argument('--max_time', type=int, default=600, help='seconds')
    subparser.add_argument('--time_step', type=int, default=60, help='seconds')
    subparser.add_argument('--no_same_axes', dest='same_axes', action='store_false')
    # plot_present_absent: Produce a figure (one figure per IOU threshold)
    subparser = subparsers.add_parser('plot_present_absent', formatter_class=ARGS_FORMATTER,
                                      parents=[common, plot_args])


def main():
    parser = argparse.ArgumentParser(formatter_class=ARGS_FORMATTER)
    _add_arguments(parser)
    global args
    args = parser.parse_args()

    dataset_names = _get_datasets(args.data)
    dataset_tasks = {
        dataset: _load_tasks(os.path.join(REPO_DIR, 'dataset', 'annotations', dataset + '.csv'))
        for dataset in dataset_names}
    # Take union of all datasets.
    tasks = {key: task for dataset in dataset_names
             for key, task in dataset_tasks[dataset].items()}

    tracker_names = _load_tracker_names()
    # Assign colors and markers alphabetically to achieve invariance across plots.
    trackers = sorted(tracker_names.keys(), key=lambda s: s.lower())
    color_list = _generate_colors(len(trackers))
    tracker_colors = dict(zip(trackers, color_list))
    tracker_markers = dict(zip(trackers, itertools.cycle(MARKERS)))

    # Each element preds[tracker] is a VideoObjectDict of TimeSeries of prediction dicts.
    # Only predictions for frames with ground-truth labels are kept.
    # This is much smaller than the predictions for all frames, and is therefore cached.
    predictions = {}
    for dataset in dataset_names:
        for tracker_ind, tracker in enumerate(trackers):
            log_context = 'tracker {}/{} {}'.format(tracker_ind + 1, len(trackers), tracker)
            cache_file = os.path.join(dataset, 'predictions', '{}.pickle'.format(tracker))
            predictions.setdefault(tracker, {}).update(oxuva.cache_pickle(
                os.path.join(args.cache_dir, 'analyze', cache_file),
                lambda: _load_predictions_and_select_frames(
                    dataset_tasks[dataset],
                    os.path.join(REPO_DIR, 'predictions', dataset, tracker),
                    log_prefix=log_context + ': '),
                ignore_existing=args.ignore_cache,
                verbose=args.verbose))

    # Each element assessments[tracker][iou] is a VideoObjectDict
    # of TimeSeries of frame assessment dicts.
    # TODO: Is it unsafe to use float (iou) as dictionary key?
    assessments = {tracker: {iou: {
        track: oxuva.assess_sequence(tasks[track].labels, predictions[tracker][track], iou)
        for track in tasks} for iou in args.iou_thresholds} for tracker in trackers}

    if args.subcommand == 'table':
        _print_statistics(assessments, trackers, tracker_names)
    elif args.subcommand == 'plot_tpr_tnr':
        _plot_tpr_tnr_overall(assessments, tasks, trackers,
                              tracker_names, tracker_colors, tracker_markers)
    elif args.subcommand == 'plot_tpr_tnr_intervals':
        _plot_tpr_tnr_intervals(assessments, tasks, trackers,
                                tracker_names, tracker_colors, tracker_markers)
    elif args.subcommand == 'plot_tpr_time':
        for iou in args.iou_thresholds:
            _plot_intervals(assessments, tasks, trackers, iou,
                            tracker_names, tracker_colors, tracker_markers)
    elif args.subcommand == 'plot_present_absent':
        for iou in args.iou_thresholds:
            _plot_present_absent(assessments, tasks, trackers, iou,
                                 tracker_names, tracker_colors, tracker_markers)


def _load_tracker_names():
    challenges = _get_challenges(args.challenge)
    union = {}
    for c in challenges:
        with open('trackers_{}.json'.format(c), 'r') as f:
            tracker_names = json.load(f)
        union.update(tracker_names)
    return union


def _get_challenges(name):
    if name == 'all':
        return ['constrained', 'open']
    else:
        return [name]


def _get_datasets(name):
    if name == 'devtest':
        return ['dev', 'test']
    else:
        return [name]


def _load_tasks(fname):
    with open(fname, 'r') as fp:
        # if fname.endswith('.json'):
        #     tracks = json.load(fp)
        if fname.endswith('.csv'):
            tracks = oxuva.load_dataset_annotations_csv(fp)
        else:
            raise ValueError('unknown extension: {}'.format(fname))
    return oxuva.map_dict(oxuva.make_task_from_track, tracks)


def _load_predictions_and_select_frames(tasks, tracker_pred_dir, log_prefix=''):
    '''Loads all predictions of a tracker and takes the subset of frames with ground truth.

    Args:
        tasks -- VideoObjectDict of Tasks.
        tracker_pred_dir -- Directory that contains files video_object.csv

    Returns:
        VideoObjectDict of SparseTimeSeries of frame assessments.
    '''
    preds = oxuva.VideoObjectDict()
    for track_num, vid_obj in enumerate(tasks.keys()):
        vid, obj = vid_obj
        task = tasks[vid_obj]
        track_name = vid + '_' + obj
        log_context = '{}object {}/{} {}'.format(
            log_prefix, track_num + 1, len(tasks), track_name)
        if args.verbose:
            print(log_context, file=sys.stderr)
        pred_file = os.path.join(tracker_pred_dir, '{}.csv'.format(track_name))
        try:
            with open(pred_file, 'r') as fp:
                pred = oxuva.load_predictions_csv(fp)
        except IOError, exc:
            if args.permissive:
                print('warning: exclude track {}: {}'.format(track_name, str(exc)), file=sys.stderr)
            else:
                raise
        pred = oxuva.subset_using_previous_if_missing(pred, task.labels.sorted_keys())
        preds[vid_obj] = pred
    return preds


def _print_statistics(assessments, trackers, names=None):
    METRICS = ['TPR', 'TNR', 'GM', 'MaxGM']
    names = names or {}
    stats = {tracker: {iou: _dataset_quality(assessments[tracker][iou])
                       for iou in args.iou_thresholds} for tracker in trackers}
    table_dir = os.path.join('analysis', args.data, args.challenge)
    _ensure_dir_exists(table_dir)
    table_file = os.path.join(table_dir, 'table.txt')
    if args.verbose:
        print('write table to {}'.format(table_file), file=sys.stderr)
    with open(table_file, 'w') as f:
        fieldnames = ['tracker'] + [
            metric + '_' + str(iou) for iou in args.iou_thresholds for metric in METRICS]
        print(','.join(fieldnames), file=f)
        for tracker in trackers:
            row = [names.get(tracker, tracker)] + [
                '{:.6g}'.format(stats[tracker][iou][metric])
                for iou in args.iou_thresholds for metric in METRICS]
            print(','.join(row), file=f)


def _plot_tpr_tnr_overall(assessments, tasks, trackers,
                          names=None, colors=None, markers=None):
    for iou in args.iou_thresholds:
        _plot_tpr_tnr('tpr_tnr_iou_{}'.format(_float2str_latex(iou)),
                      assessments, tasks, trackers, iou,
                      names=names, colors=colors, markers=markers,
                      min_time=None, max_time=None, include_score=True,
                      legend_kwargs=dict(loc='lower left', bbox_to_anchor=(0.05, 0)))


def _plot_tpr_tnr_intervals(assessments, tasks, trackers,
                            names=None, colors=None, markers=None):
    modes = ['before', 'after']
    intervals = {}
    for mode in modes:
        intervals[mode], _ = _make_intervals(args.times, mode)

    for iou in args.iou_thresholds:
        # Order by performance on all frames.
        stats = {tracker: _dataset_quality(assessments[tracker][iou]) for tracker in trackers}
        order = sorted(trackers, key=lambda t: _stats_sort_key(stats[t]), reverse=True)

        # Get stats for all plots to establish axis range.
        # Note: This means that _dataset_quality_interval() is called twice.
        max_tpr = max([max([max([
            _dataset_quality_interval(assessments[tracker][iou], tasks, min_time, max_time)['TPR']
            for tracker in trackers]) for min_time, max_time in intervals[mode]]) for mode in modes])

        for mode in modes:
            for min_time, max_time in intervals[mode]:
                base_name = 'tpr_tnr_iou_{}_interval_{}_{}'.format(
                    _float2str_latex(iou), _float2str_latex(min_time), _float2str_latex(max_time))
                _plot_tpr_tnr(base_name, assessments, tasks, trackers, iou,
                              min_time=min_time, max_time=max_time,
                              max_tpr=max_tpr, order=order, enable_posthoc=False,
                              names=names, colors=colors, markers=markers,
                              legend_kwargs=dict(loc='upper right'))


def _plot_tpr_tnr(base_name, assessments, tasks, trackers, iou_threshold,
                  min_time=None, max_time=None, include_score=False,
                  max_tpr=None, order=None, enable_posthoc=True,
                  names=None, colors=None, markers=None, legend_kwargs=None):
    names = names or {}
    colors = colors or {}
    markers = markers or {}
    legend_kwargs = legend_kwargs or {}

    for iou in args.iou_thresholds:
        stats = {tracker: _dataset_quality_interval(
            assessments[tracker][iou_threshold], tasks, min_time, max_time)
            for tracker in trackers}
        if order is None:
            order = sorted(trackers, key=lambda t: _stats_sort_key(stats[t]), reverse=True)

        plt.figure(figsize=(args.width_inches, args.height_inches))
        plt.xlabel('True Negative Rate (Absent)')
        plt.ylabel('True Positive Rate (Present)')
        if args.level_sets:
            _plot_level_sets()
        for tracker in order:
            plt.plot(
                [stats[tracker]['TNR']], [stats[tracker]['TPR']],
                label=_tracker_label(names.get(tracker, tracker), include_score, stats[tracker]),
                color=colors.get(tracker, None),
                marker=markers.get(tracker, None),
                markerfacecolor='none', markeredgewidth=2, clip_on=False)
            if args.lower_bounds:
                plt.plot(
                    [stats[tracker]['TNR'], 1], [stats[tracker]['TPR'], 0],
                    color=colors.get(tracker, None),
                    linestyle='dashed', marker='')
        if max_tpr is None:
            max_tpr = max([stats[tracker]['TPR'] for tracker in trackers])
        plt.xlim(xmin=0, xmax=1)
        plt.ylim(ymin=0, ymax=_ceil_nearest(CLEARANCE * max_tpr, 0.1))
        plt.grid(color=GRID_COLOR)
        plt.legend(**legend_kwargs)
        plot_dir = os.path.join('analysis', args.data, args.challenge)
        _ensure_dir_exists(plot_dir)
        _save_fig(os.path.join(plot_dir, base_name + '.pdf'))
        plt.gca().legend().set_visible(False)
        _save_fig(os.path.join(plot_dir, base_name + '_no_legend.pdf'))

        if enable_posthoc:
            # Add posthoc-threshold curves to figure.
            plt.legend(**legend_kwargs)
            for tracker in trackers:
                _plot_posthoc_curve(assessments[tracker][iou_threshold],
                                    marker='', color=colors.get(tracker, None))
            _save_fig(os.path.join(plot_dir, base_name + '_posthoc.pdf'))
            plt.gca().legend().set_visible(False)
            _save_fig(os.path.join(plot_dir, base_name + '_posthoc_no_legend.pdf'))


def _plot_posthoc_curve(assessments, **kwargs):
    frames = list(itertools.chain(*[series.values() for series in assessments.values()]))
    operating_points = oxuva.posthoc_threshold(frames)
    metrics = map(oxuva.quality_metrics, operating_points)
    plt.plot([point['TNR'] for point in metrics],
             [point['TPR'] for point in metrics], **kwargs)


def _plot_intervals(assessments, tasks, trackers, iou_threshold,
                    names=None, colors=None, markers=None):
    names = names or {}
    colors = colors or {}
    markers = markers or {}
    times_sec = range(0, args.max_time + 1, args.time_step)

    # Get overall stats for order in legend.
    overall_stats = {tracker: _dataset_quality(assessments[tracker][iou_threshold])
                     for tracker in trackers}
    order = sorted(trackers, key=lambda t: _stats_sort_key(overall_stats[t]), reverse=True)

    intervals = {}
    points = {}
    for mode in INTERVAL_TYPES:
        intervals[mode], points[mode] = _make_intervals(times_sec, mode)

    stats = {mode: {tracker: [
        _dataset_quality_interval(assessments[tracker][iou_threshold], tasks, min_time, max_time)
        for min_time, max_time in intervals[mode]] for tracker in trackers} for mode in INTERVAL_TYPES}
    # Get TPR for all intervals.
    tpr = {mode: {tracker: [s.get('TPR', None) for s in stats[mode][tracker]]
                  for tracker in trackers} for mode in INTERVAL_TYPES}
    # Find maximum TPR value over all plots (to have same axes).
    max_tpr = {mode: max(val for tracker in trackers for val in tpr[mode][tracker] if val is not None)
               for mode in INTERVAL_TYPES}

    for mode in INTERVAL_TYPES:
        plt.figure(figsize=(args.width_inches, args.height_inches))
        plt.xlabel(INTERVAL_AXIS_LABEL[mode])
        plt.ylabel('True Positive Rate')
        for tracker in order:
            plt.plot(1 / 60.0 * np.asfarray(points[mode]), tpr[mode][tracker],
                     label=names.get(tracker, tracker),
                     marker=markers.get(tracker, None),
                     color=colors.get(tracker, None),
                     markerfacecolor='none', markeredgewidth=2, clip_on=False)
        plt.xlim(xmin=0, xmax=args.max_time / 60.0)
        ymax = max(max_tpr.values()) if args.same_axes else max_tpr[mode]
        plt.ylim(ymin=0, ymax=_ceil_nearest(CLEARANCE * ymax, 0.1))
        plt.grid(color=GRID_COLOR)
        plot_dir = os.path.join('analysis', args.data, args.challenge)
        _ensure_dir_exists(plot_dir)
        base_name = 'tpr_time_iou_{}_interval_{}'.format(_float2str_latex(iou_threshold), mode)
        _save_fig(os.path.join(plot_dir, base_name + '_no_legend.pdf'))
        plt.legend()
        _save_fig(os.path.join(plot_dir, base_name + '.pdf'))


def _plot_present_absent(
        assessments, tasks, trackers, iou_threshold,
        names=None, colors=None, markers=None):
    names = names or {}
    colors = colors or {}
    markers = markers or {}

    # Find subset of tasks that have absent frames.
    subset_all_present = [
        key for key, task in tasks.items()
        if all([label['present'] for t, label in task.labels.items()])]
    subset_any_absent = [
        key for key, task in tasks.items()
        if not all([label['present'] for t, label in task.labels.items()])]

    stats_whole = {
        tracker: _dataset_quality(assessments[tracker][iou_threshold])
        for tracker in trackers}
    stats_all_present = {
        tracker: _dataset_quality(
            {key: assessments[tracker][iou_threshold][key] for key in subset_all_present})
        for tracker in trackers}
    stats_any_absent = {
        tracker: _dataset_quality(
            {key: assessments[tracker][iou_threshold][key] for key in subset_any_absent})
        for tracker in trackers}

    order = sorted(trackers, key=lambda t: _stats_sort_key(stats_whole[t]), reverse=True)
    max_tpr = max(max([stats_all_present[tracker]['TPR'] for tracker in trackers]),
                  max([stats_any_absent[tracker]['TPR'] for tracker in trackers]))

    plt.figure(figsize=(args.width_inches, args.height_inches))
    plt.xlabel('TPR (tracks without absent labels)')
    plt.ylabel('TPR (tracks with some absent labels)')
    for tracker in order:
        plt.plot(
            [stats_all_present[tracker]['TPR']], [stats_any_absent[tracker]['TPR']],
            label=names.get(tracker, tracker),
            color=colors.get(tracker, None),
            marker=markers.get(tracker, None),
            markerfacecolor='none', markeredgewidth=2, clip_on=False)
    plt.xlim(xmin=0, xmax=_ceil_nearest(CLEARANCE * max_tpr, 0.1))
    plt.ylim(ymin=0, ymax=_ceil_nearest(CLEARANCE * max_tpr, 0.1))
    plt.grid(color=GRID_COLOR)
    # Draw a diagonal line.
    plt.plot([0, 1], [0, 1], color=GRID_COLOR, linewidth=1, linestyle='dotted')
    plot_dir = os.path.join('analysis', args.data, args.challenge)
    _ensure_dir_exists(plot_dir)
    base_name = 'present_absent_iou_{}'.format(_float2str_latex(iou_threshold))
    # _save_fig(os.path.join(plot_dir, base_name + '_no_legend.pdf'))
    plt.legend()
    _save_fig(os.path.join(plot_dir, base_name + '.pdf'))


def _make_intervals(values, interval_type):
    '''Produces intervals and points at which to plot them.

    Returns:
        intervals, points

    Example:
        >> _make_intervals([0, 1, 2, 3], 'before')
        [(0, 1), (0, 2), (0, 3)], [1, 2, 3]

        >> _make_intervals([0, 1, 2, 3], 'after')
        [(0, inf), (1, inf), (2, inf), (3, inf)], [1, 2, 3]

        >> _make_intervals([0, 1, 2, 3], 'between')
        [(0, 1), (1, 2), (2, 3)], [0.5, 1.5, 2.5]
    '''
    if interval_type == 'before':
        intervals = [(0, x) for x in values if x > 0]
        points = [x for x in values if x > 0]
    elif interval_type == 'after':
        intervals = [(x, float('inf')) for x in values]
        points = list(values)
    elif interval_type == 'between':
        intervals = zip(values, values[1:])
        points = [0.5 * (a + b) for a, b in intervals]
    return intervals, points


def _dataset_quality(assessments):
    '''Computes the overall quality of predictions on a dataset.

    Args:
        assessments -- VideoObjectDict of SparseTimeSeries of frame assessments dicts.

    Returns:
        List that contains statistics for each interval.
    '''
    # TODO: Clean up these names?
    sequence_assessments = {
        vid_obj: oxuva.assessment_sum(assessments[vid_obj].values())
        for vid_obj in assessments}
    dataset_assessment = oxuva.assessment_sum(sequence_assessments.values())
    return oxuva.quality_metrics(dataset_assessment)


def _dataset_quality_interval(assessments, tasks, min_time_seconds, max_time_seconds):
    '''Computes the overall quality of predictions on a dataset.

    Args:
        tasks -- VideoObjectDict of Tasks.
            This is required to get the initial frame number of each track.

    Returns:
        List that contains statistics for each interval.
    '''
    min_time = None if min_time_seconds is None else FRAME_RATE * min_time_seconds
    max_time = None if max_time_seconds is None else FRAME_RATE * max_time_seconds
    assessments = {
        vid_obj: oxuva.select_interval(assessments[vid_obj], min_time, max_time,
                                       init_time=tasks[vid_obj].init_time)
        for vid_obj in assessments}
    return _dataset_quality(assessments)


def _ensure_dir_exists(dir):
    if not os.path.exists(dir):
        os.makedirs(dir)


def _generate_colors(n):
    # return [colorsys.hsv_to_rgb(i / n, s, v) for i in range(n)]
    for cmap_name in CMAP_PREFERENCE:
        cmap = matplotlib.cm.get_cmap(cmap_name)
        if n <= cmap.N:
            break
    return [cmap(float(i) / n) for i in range(n)]


def _save_fig(plot_file):
    if args.verbose:
        print('write plot to {}'.format(plot_file), file=sys.stderr)
    plt.savefig(plot_file)


def _plot_level_sets(n=10, num_points=100):
    x = np.linspace(0, 1, num_points + 1)[1:]
    for gm in np.asfarray(range(1, n)) / n:
        # gm = sqrt(x*y); y = gm^2 / x
        y = gm**2 / x
        plt.plot(x, y, color=GRID_COLOR, linewidth=1, linestyle='dotted')


def _ceil_nearest(x, step):
    '''Rounds up to nearest multiple of step.'''
    return math.ceil(x / step) * step


def _stats_sort_key(stats):
    return (stats['MaxGM'], stats['TPR'], stats['TNR'])


def _tracker_label(name, include_score, stats):
    if not include_score:
        return name
    max_at_point = abs(stats['GM'] - stats['MaxGM']) <= 1e-3
    asterisk = '*' if max_at_point else ''
    return '{} ({:.2f}{})'.format(name, stats['MaxGM'], asterisk)


def _float2str_latex(x):
    return str(x).replace('.', 'd')


if __name__ == '__main__':
    main()
