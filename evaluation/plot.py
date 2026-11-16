"""Rebuild the two publication figures from audited summary CSV, without simulations."""
import argparse
import csv
from pathlib import Path

from .common import checksums, new_directory, read_json, verify_checksums, write_json


def plot(source, output):
    verify_checksums(source)
    audit = read_json(source / 'audit.json')
    if not audit['passed']:
        raise ValueError('Refusing to plot unaudited results')
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
    from matplotlib.ticker import PercentFormatter

    with (source / 'summary.csv').open(encoding='utf-8', newline='') as handle:
        rows = list(csv.DictReader(handle))
    data = {(r['config_id'], r['metric']): r for r in rows}
    labels = ['RS(2,4)', 'RS(3,6)', 'Two replicas']
    schemes = ['rs-2-4', 'rs-3-6', 'replication-1-2']
    plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 11,
                         'axes.spines.top': False, 'axes.spines.right': False,
                         'svg.hashsalt': 'member3-evaluation'})
    out = new_directory(output)

    def values(environment, metric, scale=1):
        records = [data[f'{environment}-{scheme}', metric] for scheme in schemes]
        means = [float(r['mean']) / scale for r in records]
        errors = [[float(r['mean']) / scale - float(r['ci_low']) / scale for r in records],
                  [float(r['ci_high']) / scale - float(r['mean']) / scale for r in records]]
        return means, errors

    for figure_number in (1, 2):
        fig, axes = plt.subplots(1, 2, figsize=(11.4, 4.8), sharey=True)
        for ax, environment in zip(axes, ('default', 'stress')):
            ax.set_title(environment.capitalize(), loc='left', fontweight='bold', pad=14)
            ax.set_xticks(range(3), labels)
            ax.grid(axis='y', alpha=.2)
            ax.set_axisbelow(True)
            if figure_number == 1:
                for offset, metric, label, color in ((-.18, 'recovery_probability', 'Recovered', '#167D8D'),
                                                     (.18, 'acceptance_probability', 'Accepted', '#E38B45')):
                    means, errors = values(environment, metric)
                    ax.bar([x + offset for x in range(3)], means, width=.32, yerr=errors,
                           capsize=3, color=color, label=label, error_kw={'elinewidth': 1})
                ax.set_ylim(0, 1.12)
                ax.yaxis.set_major_formatter(PercentFormatter(1))
            else:
                means, errors = values(environment, 'communication_bytes', 1_000_000)
                bars = ax.bar(range(3), means, yerr=errors, width=.55, capsize=4, color='#167D8D')
                for bar, mean in zip(bars, means):
                    ax.annotate(f'{mean:.2f}', (bar.get_x() + bar.get_width()/2, bar.get_height()),
                                xytext=(0, 12), textcoords='offset points', ha='center', fontsize=10)
                ax.set_ylim(0, 4.6)
        if figure_number == 1:
            axes[0].set_ylabel('Probability per attempt')
            axes[0].legend(loc='lower right', bbox_to_anchor=(1, 1.02), ncol=2, frameon=False, fontsize=10)
            title = 'Recovery and acceptance at equal storage'
            filename = 'figure1_recovery_acceptance'
            detail = 'Acceptance requires a correct recovery, at least 2/3 ACCEPT votes and completion by 250 ms.'
        else:
            axes[0].set_ylabel('Mean communication per attempt (MB)')
            title = 'Communication cost including failed attempts'
            filename = 'figure2_communication'
            detail = '1 MB = 1,000,000 bytes. Request + received response + evidence; transport and validator traffic excluded.'
        fig.suptitle(title, fontsize=17, fontweight='bold', x=.07, ha='left')
        caption = f'Means over {audit["seeds_per_configuration"]} seeds; error bars: 95% Student t CI over seed means. All schemes: 2x storage.'
        fig.text(.07, .075, caption, fontsize=9, color='#444444')
        fig.text(.07, .035, detail, fontsize=8.5, color='#444444')
        fig.subplots_adjust(left=.07, right=.98, bottom=.20, top=.81, wspace=.10)
        fig.savefig(out / f'{filename}.png', dpi=240, metadata={'Software': 'Member 3 evaluation'})
        fig.savefig(out / f'{filename}.svg', metadata={'Date': None, 'Creator': 'Member 3 evaluation'})
        plt.close(fig)
    write_json(out / 'checksums.json', checksums(out))
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=Path('artifacts/part3/analysis'))
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        plot(args.input, args.output)
        print(f'Two figures rebuilt from CSV into {args.output}')
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')


if __name__ == '__main__':
    main()
