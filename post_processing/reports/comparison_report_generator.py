"""
Code to automatically generate a comparison report from thw or more direcotries
containing a set of performance results in the intermediate format in PR 319.

The report will be generated in markdowm format using the create_report()
method and the resulting file saved to the specified output directory.

Optionally the markdown report can be converted to a pdf using pandoc by
calling save_as_pdf()

The pdf file will have the same name as the markdown file, and be saved in the same output
directory.
"""

import subprocess
from logging import Logger, getLogger
from pathlib import Path
from typing import Optional

from post_processing.common import (
    PLOT_FILE_EXTENSION_WITH_DOT,
    TITLE_CONVERSION,
    calculate_percent_difference_to_baseline,
    get_blocksize_percentage_operation_from_file_name,
    get_date_time_string,
    get_latency_throughput_from_file,
    strip_confidential_data_from_yaml,
)
from post_processing.plotter.directory_comparison_plotter import DirectoryComparisonPlotter
from post_processing.reports.report_generator import ReportGenerator

log: Logger = getLogger("cbt")


class ComparisonReportGenerator(ReportGenerator):
    def _generate_report_title(self) -> str:
        title: str = f"Comparitive Performance Report for {' vs '.join(self._build_strings)}"
        return title

    def _add_plots(self) -> None:
        self._report.new_header(level=1, title="Response Curves")
        empty_table_header: list[str] = ["", ""]
        image_tables: dict[str, list[str]] = {}

        for _, operation in TITLE_CONVERSION.items():
            image_tables[operation] = empty_table_header.copy()

        for image_file in self._plot_files:
            # The comparison plot files have a different name format:
            #     Comparison_<blocksize>_<percent>_<operation>
            # so we need o split the Comparison_ from the front before calling the common method
            (blocksize, percent, operation) = get_blocksize_percentage_operation_from_file_name(
                image_file.stem[len("Comparison_") :]
            )
            title: str = f"{blocksize} {percent} {operation}"

            image_line: str = self._report.new_inline_image(
                text=title, path=f"{self._plots_directory.parts[-1]}/{image_file.parts[-1]}"
            )
            anchor: str = f'<a name="{image_file.stem[len("Comparison_") :].replace("_", "-")}"></a>'

            image_line = f"{anchor}{image_line}"

            image_tables[operation].append(image_line)

        # Create the correct sections and add a table for each section to the report

        for section in image_tables.keys():
            # We don't want to display a section if it doesn't contain any plots
            if len(image_tables[section]) > len(empty_table_header):
                self._report.new_header(level=2, title=section)
                table_images = image_tables[section]

                # We need to calculate the rumber of rows, but new_table() requires the
                # exact number of items to fill the table, so we may need to add a dummy
                # entry at the end
                number_of_rows: int = len(table_images) // 2
                if len(table_images) % 2 > 0:
                    number_of_rows += 1
                    table_images.append("")
                self._report.new_table(columns=2, rows=number_of_rows, text=table_images, text_align="center")

    def _add_summary_table(self) -> None:
        self._report.new_header(level=1, title=f"Comparison summary for {' vs '.join(self._build_strings)}")
        # We cannot use the mdutils table object here as it can only justify
        # all the colums in the same way, and we want to justify different
        # columns differently.
        # Therefore we have to build the table ourselves

        data_tables: dict[str, list[str]] = {}
        for _, operation in TITLE_CONVERSION.items():
            data_tables[operation] = []

        (table_header, table_justfication_string) = self._generate_table_headers()
        self._generate_table_rows(data_tables)

        for operation in data_tables.keys():
            if data_tables[operation]:
                self._report.new_line(text=f"|{operation}|{table_header}")
                self._report.new_line(text=table_justfication_string)

            for line in data_tables[operation]:
                self._report.new_line(text=line)
            self._report.new_line()

    def _add_configuration_yaml_files(self) -> None:
        self._report.new_header(level=1, title="Configuration yaml files")

        yaml_paragraph: str = (
            "Only yaml files that differ by more than 20 lines from the yaml file for the "
            + "baseline directory will be added here in addition to the baseline yaml"
        )

        self._report.new_paragraph(yaml_paragraph)
        self._report.new_line()

        yaml_files: list[Path] = self._find_configuration_yaml_files()

        base_yaml_file: Path = yaml_files.pop(0)
        self._add_yaml_file_title_and_contents(base_yaml_file)

        for yaml_file in yaml_files:
            if self._yaml_file_has_more_that_20_differences(base_yaml_file, yaml_file):
                self._add_yaml_file_title_and_contents(yaml_file)

    def _add_yaml_file_title_and_contents(self, file_path: Path) -> None:
        """
        Add a title heading and the contents of a yaml file to the report
        """
        self._report.new_header(level=2, title=f"{file_path.parts[-2]}")

        file_contents: str = file_path.read_text()
        safe_contents = strip_confidential_data_from_yaml(file_contents)
        markdown_string: str = f"```{safe_contents}```"
        self._report.new_paragraph(markdown_string)
        file_contents: str = file_path.read_text()
        safe_contents = strip_confidential_data_from_yaml(file_contents)
        markdown_string: str = f"```{safe_contents}```"
        self._report.new_paragraph(markdown_string)

    def _generate_report_name(self) -> str:
        datetime_string: str = get_date_time_string()
        output_file_name: str = f"comparitive_performance_report_{datetime_string}.{self.MARKDOWN_FILE_EXTENSION}"
        return output_file_name

    def _find_and_sort_file_paths(self, paths: list[Path], search_pattern: str, index: Optional[int] = 0) -> list[Path]:
        sorted_paths: list[Path] = []
        unsorted_paths: list[Path] = []

        for directory in paths:
            unsorted_paths.extend(list(directory.glob(search_pattern)))

        assert index is not None
        sorted_paths = self._sort_list_of_paths(unsorted_paths, index)

        return sorted_paths

    def _find_and_sort_plot_files(self) -> list[Path]:
        """
        Find all the plot files in the directory. That is any file that
        has the .png file extension

        This overrides the one in the ReportGenerator as the comparison plot
        files have a different naming convention:
            Comparison_<blocksize>B_<read%>_<write%>_<operation>.png
        """
        return self._find_and_sort_file_paths(
            paths=[self._plots_directory], search_pattern=f"*{PLOT_FILE_EXTENSION_WITH_DOT}", index=1
        )

    def _find_configuration_yaml_files(self) -> list[Path]:
        file_paths: list[Path] = []

        for directory in self._archive_directories:
            file_paths.extend(directory.glob("**/cbt_config.yaml"))

        return file_paths

    def _create_comparison_plots(self) -> None:
        """
        Generate the comparison plots and save them in the correct place
        """
        plotter: DirectoryComparisonPlotter = DirectoryComparisonPlotter(
            output_directory=f"{self._plots_directory}",
            directories=[f"{directory}" for directory in self._archive_directories],
        )
        plotter.draw_and_save()

    def _copy_images(self) -> None:
        self._create_comparison_plots()

    def _generate_table_headers(self) -> tuple[str, str]:
        """
        Generate the header lines for the table
        """
        # The first directory is always the baseline, so we want to get it out of the way first
        table_header: str = f"{self._data_directories.pop(0).parts[-2]}|"
        table_justfication_string: str = "| :--- | ---: |"

        if len(self._data_directories) < 2:
            for directory in self._data_directories:
                table_header += f"{directory.parts[-2]}|%change throughput|%change latency|"
                table_justfication_string += " ---: | ---: | ---: |"
        else:
            for directory in self._data_directories:
                table_header += f"{directory.parts[-2]}|%change|"
                table_justfication_string += " ---: | ---: |"

        return (table_header, table_justfication_string)

    def _generate_table_rows(self, data_tables: dict[str, list[str]]) -> None:
        """
        Generate the data for all the rows in the table
        """
        for file_name, file_paths in self._data_files.items():
            (blocksize, percentage, operation) = get_blocksize_percentage_operation_from_file_name(file_name)
            data_string: str = f"|[{blocksize}"
            if percentage:
                data_string += f"_{percentage}"

            data_string += f"](#{file_name.replace('_', '-')})|"

            (baseline_max_throughput, baseline_latency_ms) = get_latency_throughput_from_file(file_paths.pop(0))

            if len(self._data_directories) < 2:
                data_string += f"{baseline_max_throughput}@{baseline_latency_ms}ms|"
            else:
                data_string += f"{baseline_max_throughput.split(' ')[0]}@{baseline_latency_ms}ms|"

            for file_path in file_paths:
                (max_throughput, latency_ms) = get_latency_throughput_from_file(file_path)
                throughput_percentage_difference: str = calculate_percent_difference_to_baseline(
                    baseline=baseline_max_throughput, comparison=max_throughput
                )

                if len(self._data_directories) < 2:
                    latency_percentage_difference: str = calculate_percent_difference_to_baseline(
                        baseline=baseline_latency_ms, comparison=latency_ms
                    )
                    data_string += f"{max_throughput}@{latency_ms}ms|{throughput_percentage_difference}|{latency_percentage_difference}|"
                else:
                    data_string += f"{max_throughput.split(' ')[0]}@{latency_ms}|{throughput_percentage_difference}|"

            data_tables[operation].append(data_string)

    def _yaml_file_has_more_that_20_differences(self, base_file: Path, comparison_file: Path) -> bool:
        """
        If there are more that 20 differences between base and comparison then
        return True, otherwise False
        """
        diff_command: str = (
            f"/usr/bin/env diff -wy --suppress-common-lines {str(base_file)} {str(comparison_file)} | wc -l"
        )

        output: bytes
        try:
            output = subprocess.check_output(diff_command, shell=True)
        except subprocess.CalledProcessError:
            return False

        output_as_string: str = output.decode().strip()

        return int(output_as_string) > 20
